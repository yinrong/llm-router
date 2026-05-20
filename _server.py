#!/usr/bin/env python3

import asyncio
import json
import logging
import random
import ssl
import string
import os

import aiohttp
import config
from c_x_client import XClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("app")


def _random_padding() -> str:
    length = random.randint(16, 128)
    return "".join(random.choices(string.ascii_letters + string.digits, k=length))


def build_x_client_for_c() -> XClient | None:
    if not config.GROUP_ID:
        return None
    config.ROLE = "C"
    client_id = (
        os.environ.get("CLIENT_ID_C")
        or config.CLIENT_ID
        or config.load_or_create_client_id()
    )
    return XClient(
        x_base_url=config.X_BASE_URL,
        group_id=config.GROUP_ID,
        client_id=client_id,
    )


class Worker:
    def __init__(self, x_client: XClient | None = None):
        self.session: aiohttp.ClientSession | None = None
        self._running = True
        self.x_client = x_client if x_client is not None else build_x_client_for_c()
        # If no X client (no group_id), behave as before — always active.
        if self.x_client is None:
            self._always_active_event = asyncio.Event()
            self._always_active_event.set()

    @property
    def _active_event(self) -> asyncio.Event:
        if self.x_client is not None:
            return self.x_client.should_be_active
        return self._always_active_event

    async def start(self):
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE

        connector = aiohttp.TCPConnector(ssl=ssl_ctx)
        self.session = aiohttp.ClientSession(connector=connector)

        if self.x_client is not None:
            try:
                await self.x_client.start()
            except Exception as e:
                log.warning("X client start failed: %s", e)

        backoff = config.RECONNECT_BASE
        try:
            while self._running:
                try:
                    # Wait until X says we are the active C.
                    await self._wait_active_or_stop()
                    if not self._running:
                        break
                    await self._connect()
                    backoff = config.RECONNECT_BASE
                except Exception as e:
                    log.warning("Retrying in %ds (%s)", backoff, type(e).__name__)

                if not self._running:
                    break
                await asyncio.sleep(backoff + random.uniform(0, 2))
                backoff = min(backoff * 2, config.RECONNECT_MAX)
        finally:
            if self.x_client is not None:
                try:
                    await self.x_client.stop()
                except Exception:
                    pass
            await self.session.close()

    async def _wait_active_or_stop(self):
        # Cooperative wait: returns when active OR _running flips false.
        while self._running and not self._active_event.is_set():
            try:
                await asyncio.wait_for(self._active_event.wait(), timeout=0.5)
                return
            except asyncio.TimeoutError:
                continue

    @staticmethod
    def _ws_url() -> str:
        """Derive the relay WS URL from X_BASE_URL (the canonical X/B address).

        Production:  https://yinaisvr.duckdns.org → wss://yinaisvr.duckdns.org/ws/notifications
        Tests / dev: http://127.0.0.1:PORT         → ws://127.0.0.1:PORT/ws/notifications
        """
        from urllib.parse import urlparse
        p = urlparse(config.X_BASE_URL)
        scheme = "wss" if p.scheme == "https" else "ws"
        return f"{scheme}://{p.netloc}{config.WS_PATH}"

    async def _connect(self):
        url = self._ws_url()
        headers = {"Cookie": f"_sid={config.TUNNEL_SECRET}"}

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

    async def _watch_inactivity(self, ws: aiohttp.ClientWebSocketResponse):
        """Close the WS as soon as we're told to stand by."""
        try:
            while not ws.closed and self._running:
                if not self._active_event.is_set():
                    log.info("No longer active; closing tunnel")
                    await ws.close()
                    return
                await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            pass

    async def _heartbeat_loop(self, ws: aiohttp.ClientWebSocketResponse):
        try:
            while not ws.closed:
                interval = random.uniform(config.HEARTBEAT_MIN, config.HEARTBEAT_MAX)
                await asyncio.sleep(interval)
                if not ws.closed:
                    await ws.send_str(json.dumps({
                        "type": "ping",
                        "padding": _random_padding(),
                    }))
        except asyncio.CancelledError:
            pass

    async def _handle_message(self, ws: aiohttp.ClientWebSocketResponse, raw: str):
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

        internal_url = config.INTERNAL_LLM_BASE.rstrip("/") + path

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
    ):
        async with self.session.request(
            method, url, json=body, headers=fwd_headers,
            timeout=aiohttp.ClientTimeout(total=config.REQUEST_TIMEOUT),
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
    ):
        async with self.session.request(
            method, url, json=body, headers=fwd_headers,
            timeout=aiohttp.ClientTimeout(total=config.REQUEST_TIMEOUT),
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


async def main():
    worker = Worker()
    await worker.start()


if __name__ == "__main__":
    asyncio.run(main())
