"""TunnelWorker: C's WebSocket tunnel to X.

Receives LLM API requests from X and forwards them to the internal LLM.
Uses CSettings for all configuration — no global config reads.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import ssl
import string
from typing import Optional

import aiohttp

from c.settings import CSettings
from c.x_client import XClient

log = logging.getLogger("c.tunnel")


def _random_padding() -> str:
    length = random.randint(16, 128)
    return "".join(random.choices(string.ascii_letters + string.digits, k=length))


class TunnelWorker:
    def __init__(self, settings: CSettings, x_client: Optional[XClient] = None):
        self._settings = settings
        self.session: Optional[aiohttp.ClientSession] = None
        self._running = True

        if x_client is not None:
            self.x_client: Optional[XClient] = x_client
        elif settings.group_id:
            self.x_client = XClient(settings)
        else:
            self.x_client = None

        if self.x_client is None:
            self._always_active = asyncio.Event()
            self._always_active.set()

    @property
    def _active_event(self) -> asyncio.Event:
        if self.x_client is not None:
            return self.x_client.should_be_active
        return self._always_active

    async def start(self) -> None:
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE

        connector = aiohttp.TCPConnector(ssl=ssl_ctx)
        self.session = aiohttp.ClientSession(connector=connector)

        if self.x_client is not None:
            try:
                updated = await self.x_client.start()
                self._settings = updated
            except Exception as e:
                log.warning("X client start failed: %s", e)

        backoff = self._settings.reconnect_base
        try:
            while self._running:
                try:
                    await self._wait_active_or_stop()
                    if not self._running:
                        break
                    await self._connect()
                    backoff = self._settings.reconnect_base
                except Exception as e:
                    log.warning("Retrying in %ds (%s)", backoff, type(e).__name__)

                if not self._running:
                    break
                await asyncio.sleep(backoff + random.uniform(0, 2))
                backoff = min(backoff * 2, self._settings.reconnect_max)
        finally:
            if self.x_client is not None:
                try:
                    await self.x_client.stop()
                except Exception:
                    pass
            if self.session:
                await self.session.close()

    async def _wait_active_or_stop(self) -> None:
        while self._running and not self._active_event.is_set():
            try:
                await asyncio.wait_for(self._active_event.wait(), timeout=0.5)
                return
            except asyncio.TimeoutError:
                continue

    async def _connect(self) -> None:
        url = self._settings.ws_url
        headers = {"Cookie": f"_sid={self._settings.tunnel_secret}"}
        log.info("Starting")
        async with self.session.ws_connect(url, headers=headers, heartbeat=None) as ws:
            log.info("Ready")
            heartbeat_task = asyncio.create_task(self._heartbeat_loop(ws))
            inactivity_task = asyncio.create_task(self._watch_inactivity(ws))
            try:
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        asyncio.create_task(self._handle_message(ws, msg.data))
                    elif msg.type == aiohttp.WSMsgType.ERROR:
                        log.error("Error: %s", ws.exception())
                        break
            finally:
                heartbeat_task.cancel()
                inactivity_task.cancel()

    async def _watch_inactivity(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        try:
            while not ws.closed and self._running:
                if not self._active_event.is_set():
                    log.info("No longer active; closing tunnel")
                    await ws.close()
                    return
                await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            pass

    async def _heartbeat_loop(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        try:
            while not ws.closed:
                interval = random.uniform(
                    self._settings.heartbeat_min, self._settings.heartbeat_max
                )
                await asyncio.sleep(interval)
                if not ws.closed:
                    await ws.send_str(json.dumps({
                        "type": "ping",
                        "padding": _random_padding(),
                    }))
        except asyncio.CancelledError:
            pass

    async def _handle_message(self, ws: aiohttp.ClientWebSocketResponse, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return

        if msg.get("type") != "request":
            return

        req_id = msg["id"]
        method = msg.get("method", "POST")
        path = msg.get("path", "/v1/chat/completions")
        headers = msg.get("headers", {})
        body = msg.get("body", {})
        is_stream = body.get("stream", False)

        internal_url = self._settings.internal_llm_base.rstrip("/") + path

        try:
            if is_stream:
                await self._forward_stream(ws, req_id, method, internal_url, headers, body)
            else:
                await self._forward_normal(ws, req_id, method, internal_url, headers, body)
        except Exception as e:
            log.error("Failed: %s", e)
            await ws.send_str(json.dumps({
                "type": "response",
                "id": req_id,
                "status": 502,
                "body": {"error": {"message": str(e), "type": "proxy_error"}},
            }))

    async def _forward_normal(
        self, ws: aiohttp.ClientWebSocketResponse,
        req_id: str, method: str, url: str, fwd_headers: dict, body: dict,
    ) -> None:
        async with self.session.request(
            method, url, json=body, headers=fwd_headers,
            timeout=aiohttp.ClientTimeout(total=self._settings.request_timeout),
        ) as resp:
            resp_body = await resp.json()
            await ws.send_str(json.dumps({
                "type": "response",
                "id": req_id,
                "status": resp.status,
                "headers": {"content-type": resp.headers.get("content-type", "application/json")},
                "body": resp_body,
            }))

    async def _forward_stream(
        self, ws: aiohttp.ClientWebSocketResponse,
        req_id: str, method: str, url: str, fwd_headers: dict, body: dict,
    ) -> None:
        async with self.session.request(
            method, url, json=body, headers=fwd_headers,
            timeout=aiohttp.ClientTimeout(total=self._settings.request_timeout),
        ) as resp:
            async for line in resp.content:
                decoded = line.decode() if isinstance(line, bytes) else line
                if decoded.strip():
                    await ws.send_str(json.dumps({
                        "type": "stream_chunk",
                        "id": req_id,
                        "data": decoded,
                    }))
            await ws.send_str(json.dumps({
                "type": "stream_end",
                "id": req_id,
            }))
