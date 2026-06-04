"""Multi-tenant relay for X.

Handles WebSocket tunnels from C clients and routes API requests from A
to the correct group's active tunnel. Merged into X so users only need
one public server.

Data path: A → X(/g/{group_id}/*) → WS tunnel → C → LLM.

WS auth: Cookie _sid={tunnel_secret}.  Lookup group by secret in sqlite.
Wrong secret → 404 (security: do not reveal whether path exists).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid

import aiohttp
from aiohttp import web

import config
from x import db as xdb

log = logging.getLogger("relay")

# Header allowlist forwarded to C.  Tested by test_header_allowlist.  DO NOT loosen.
_ALLOWED_HEADERS = frozenset(
    ("authorization", "x-api-key", "content-type", "anthropic-version", "anthropic-beta")
)


class MultiTenantRelay:
    def __init__(self, db):
        self._db = db
        # group_id → active WS from C
        self.tunnels: dict[str, web.WebSocketResponse] = {}
        # group_id → {req_id → Future}
        self.pending: dict[str, dict[str, asyncio.Future]] = {}
        # group_id → {req_id → Queue}
        self.stream_queues: dict[str, dict[str, asyncio.Queue]] = {}
        # group_id → asyncio.Lock (serialise tunnel replacement)
        self._group_locks: dict[str, asyncio.Lock] = {}

    # ── helpers ──────────────────────────────────────────────────────────────
    def _lock(self, group_id: str) -> asyncio.Lock:
        if group_id not in self._group_locks:
            self._group_locks[group_id] = asyncio.Lock()
        return self._group_locks[group_id]

    # ── static pages ─────────────────────────────────────────────────────────
    async def handle_index(self, request: web.Request) -> web.Response:
        static_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "index.html")
        if os.path.exists(static_path):
            return web.FileResponse(static_path)
        return web.Response(text="Welcome", content_type="text/html")

    async def handle_catch_all(self, request: web.Request) -> web.Response:
        return await self.handle_index(request)

    # ── WS tunnel (C → X) ────────────────────────────────────────────────────
    async def handle_websocket(self, request: web.Request) -> web.WebSocketResponse:
        cookie = request.headers.get("Cookie", "")
        token = next(
            (p.split("=", 1)[1] for p in cookie.split(";") if p.strip().startswith("_sid=")), ""
        )
        group = xdb.get_group_by_secret(self._db, token)
        if group is None:
            log.warning("Tunnel auth failed from %s (unknown secret)", request.remote)
            raise web.HTTPNotFound()  # 404: do not reveal endpoint existence

        group_id = group["group_id"]
        ws = web.WebSocketResponse(heartbeat=None)
        await ws.prepare(request)
        log.info("Tunnel connected: group=%s from %s", group_id, request.remote)

        async with self._lock(group_id):
            old = self.tunnels.get(group_id)
            if old is not None and not old.closed:
                await old.close()
            self.tunnels[group_id] = ws

        try:
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    await self._handle_tunnel_msg(group_id, msg.data)
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    log.error("Tunnel error group=%s: %s", group_id, ws.exception())
        finally:
            log.info("Tunnel disconnected: group=%s", group_id)
            async with self._lock(group_id):
                if self.tunnels.get(group_id) is ws:
                    del self.tunnels[group_id]
            # Fail all pending requests for this group
            for fut in (self.pending.pop(group_id, {}) or {}).values():
                if not fut.done():
                    fut.set_exception(ConnectionError("tunnel disconnected"))
            for q in (self.stream_queues.pop(group_id, {}) or {}).values():
                await q.put(None)

        return ws

    async def _handle_tunnel_msg(self, group_id: str, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return

        msg_type = msg.get("type")
        req_id = msg.get("id")
        group_pending = self.pending.get(group_id, {})
        group_queues = self.stream_queues.get(group_id, {})

        if msg_type == "pong":
            return

        if msg_type == "response" and req_id in group_pending:
            fut = group_pending.pop(req_id)
            if not fut.done():
                fut.set_result(msg)

        elif msg_type == "stream_chunk" and req_id in group_queues:
            await group_queues[req_id].put(msg.get("data", ""))

        elif msg_type == "stream_end" and req_id in group_queues:
            await group_queues[req_id].put(None)

    # ── API relay (A → X → C) ────────────────────────────────────────────────
    async def handle_api(self, request: web.Request) -> web.StreamResponse:
        started_at = time.time()
        group_id = request.match_info.get("group_id", "")
        path = "/" + (request.match_info.get("path", "") or "")

        tunnel = self.tunnels.get(group_id)
        if tunnel is None or tunnel.closed:
            self._record_audit(group_id, request.method, path, 502, started_at, error_type="no_tunnel")
            return web.json_response(
                {"error": {"message": "service unavailable", "type": "server_error"}},
                status=502,
            )

        try:
            body = await request.json()
        except Exception:
            body = {}

        is_stream = body.get("stream", False)
        req_id = uuid.uuid4().hex

        tunnel_msg = json.dumps({
            "type": "request",
            "id": req_id,
            "method": request.method,
            "path": path,
            "headers": {
                k: v
                for k, v in request.headers.items()
                if k.lower() in _ALLOWED_HEADERS
            },
            "body": body,
        })

        if is_stream:
            return await self._handle_stream(request, group_id, req_id, tunnel, tunnel_msg, started_at, path)
        else:
            return await self._handle_normal(request, group_id, req_id, tunnel, tunnel_msg, started_at, path)

    async def _handle_normal(
        self, request: web.Request, group_id: str, req_id: str,
        tunnel: web.WebSocketResponse, tunnel_msg: str, started_at: float, path: str,
    ) -> web.Response:
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self.pending.setdefault(group_id, {})[req_id] = fut

        try:
            await tunnel.send_str(tunnel_msg)
            result = await asyncio.wait_for(fut, timeout=config.REQUEST_TIMEOUT)
        except asyncio.TimeoutError:
            self.pending.get(group_id, {}).pop(req_id, None)
            self._record_audit(group_id, request.method, path, 504, started_at, error_type="timeout")
            return web.json_response(
                {"error": {"message": "request timeout", "type": "server_error"}},
                status=504,
            )
        except Exception as e:
            self.pending.get(group_id, {}).pop(req_id, None)
            self._record_audit(group_id, request.method, path, 502, started_at, error_type=type(e).__name__)
            return web.json_response(
                {"error": {"message": str(e), "type": "server_error"}},
                status=502,
            )

        status = result.get("status", 200)
        resp_body = result.get("body")
        resp_headers = result.get("headers", {})
        content_type = resp_headers.get("content-type", "application/json")

        self._record_audit(group_id, request.method, path, status, started_at, upstream_status=status)
        if isinstance(resp_body, (dict, list)):
            return web.json_response(resp_body, status=status)
        return web.Response(text=str(resp_body), status=status, content_type=content_type)

    async def _handle_stream(
        self, request: web.Request, group_id: str, req_id: str,
        tunnel: web.WebSocketResponse, tunnel_msg: str, started_at: float, path: str,
    ) -> web.StreamResponse:
        queue: asyncio.Queue = asyncio.Queue()
        self.stream_queues.setdefault(group_id, {})[req_id] = queue

        try:
            await tunnel.send_str(tunnel_msg)
        except Exception as e:
            self.stream_queues.get(group_id, {}).pop(req_id, None)
            self._record_audit(group_id, request.method, path, 502, started_at, error_type=type(e).__name__)
            return web.json_response(
                {"error": {"message": str(e), "type": "server_error"}},
                status=502,
            )

        response = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
        await response.prepare(request)

        try:
            while True:
                try:
                    chunk = await asyncio.wait_for(queue.get(), timeout=config.REQUEST_TIMEOUT)
                except asyncio.TimeoutError:
                    break
                if chunk is None:
                    break
                await response.write(chunk.encode() if isinstance(chunk, str) else chunk)
        finally:
            self.stream_queues.get(group_id, {}).pop(req_id, None)

        self._record_audit(group_id, request.method, path, 200, started_at)
        return response

    # ── audit (in-process, direct db write) ──────────────────────────────────
    def _record_audit(
        self, group_id: str, method: str, path: str, status: int, started_at: float,
        *, error_type: str | None = None, upstream_status: int | None = None,
    ) -> None:
        if not group_id:
            return
        try:
            latency_ms = max(0, int((time.time() - started_at) * 1000))
            xdb.insert_audit_events(self._db, group_id, None, [{
                "ts": int(time.time()),
                "method": method,
                "path": path,
                "status": status,
                "latency_ms": latency_ms,
                "upstream_status": upstream_status,
                "error_type": error_type,
            }])
        except Exception:
            pass
