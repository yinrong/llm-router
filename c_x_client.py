"""C's client for the X coordinator.

Responsibilities:
- Register C with X on startup (POST /api/register/c). Cache the returned
  relay_addr/port/tunnel_secret.
- Election polling: POST /api/elect/{group_id} every ELECTION_POLL_INTERVAL.
  Sets/clears `should_be_active` so the WS Worker only connects when active.
- Heartbeat (independent of election poll).
- Self-update placeholder (polls /api/version/c; non-blocking, low frequency).

Failures here never crash the WS Worker; they just log and retry.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import socket
from typing import Any

import aiohttp

import config

log = logging.getLogger("c.x")


class XClient:
    def __init__(
        self,
        *,
        x_base_url: str,
        group_id: str,
        client_id: str,
        version: str = "0.1.0",
    ):
        self.x_base_url = x_base_url.rstrip("/")
        self.group_id = group_id
        self.client_id = client_id
        self.version = version
        self.session: aiohttp.ClientSession | None = None

        self.relay_addr: str | None = None
        self.relay_port: int | None = None
        self.tunnel_secret: str | None = None
        self.election_poll: int = config.ELECTION_POLL_INTERVAL

        self.should_be_active = asyncio.Event()
        self._tasks: list[asyncio.Task] = []
        self._stopped = False
        self._cache_path = os.path.join(config.CACHE_DIR, "c-tunnel.json")

    # ── lifecycle ────────────────────────────────────────────────────────────
    async def start(self) -> None:
        if self.session is None:
            self.session = aiohttp.ClientSession()
        ok = await self.register()
        if not ok:
            self._load_cached()
        self._tasks.append(asyncio.create_task(self._election_loop()))
        self._tasks.append(asyncio.create_task(self._heartbeat_loop()))
        self._tasks.append(asyncio.create_task(self._self_update_loop()))

    async def stop(self) -> None:
        self._stopped = True
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self._tasks.clear()
        if self.session:
            await self.session.close()
            self.session = None

    # ── X interactions ───────────────────────────────────────────────────────
    async def register(self) -> bool:
        if not self.group_id:
            log.warning("GROUP_ID not configured, skipping X registration")
            return False
        try:
            async with self.session.post(
                f"{self.x_base_url}/api/register/c",
                json={
                    "group_id": self.group_id,
                    "client_id": self.client_id,
                    "version": self.version,
                    "hostname": socket.gethostname(),
                },
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    log.warning("X register/c returned %s", resp.status)
                    return False
                body = await resp.json()
        except Exception as e:
            log.warning("X register/c failed: %s", e)
            return False

        self.relay_addr = body.get("relay_addr") or None
        self.relay_port = int(body.get("relay_port") or 0) or None
        self.tunnel_secret = body.get("tunnel_secret") or None
        self.election_poll = int(body.get("election_poll") or self.election_poll)
        if self.relay_addr and self.relay_port:
            # Mirror into config so existing _server code reads correctly.
            config.RELAY_ADDR = self.relay_addr
            config.RELAY_PORT = self.relay_port
        if self.tunnel_secret:
            config.TUNNEL_SECRET = self.tunnel_secret
        self._save_cached()
        log.info("Registered with X (group=%s, relay=%s:%s)", self.group_id, self.relay_addr, self.relay_port)
        return True

    async def _election_loop(self) -> None:
        try:
            while not self._stopped:
                await self._claim_active_once()
                # Sleep with small jitter
                jitter = random.uniform(0, 0.3)
                await asyncio.sleep(max(1, self.election_poll) + jitter)
        except asyncio.CancelledError:
            pass

    async def _claim_active_once(self) -> None:
        if not self.group_id:
            # Without X, fall back to "always active" so dev/local still works.
            self.should_be_active.set()
            return
        try:
            async with self.session.post(
                f"{self.x_base_url}/api/elect/{self.group_id}",
                json={"client_id": self.client_id},
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status == 404:
                    # We may have been wiped from X; re-register and retry next tick.
                    await self.register()
                    return
                if resp.status >= 400:
                    return
                body = await resp.json()
        except Exception as e:
            log.debug("election poll failed: %s", e)
            return

        if body.get("active"):
            if not self.should_be_active.is_set():
                log.info("Elected ACTIVE (group=%s)", self.group_id)
            self.should_be_active.set()
        else:
            if self.should_be_active.is_set():
                log.info("Lost active to %s; standing by", body.get("active_client_id"))
            self.should_be_active.clear()

    async def _heartbeat_loop(self) -> None:
        try:
            while not self._stopped:
                await asyncio.sleep(config.X_HEARTBEAT_INTERVAL)
                if self._stopped:
                    break
                try:
                    async with self.session.post(
                        f"{self.x_base_url}/api/heartbeat",
                        json={"client_id": self.client_id, "role": "C", "group_id": self.group_id},
                        timeout=aiohttp.ClientTimeout(total=5),
                    ) as resp:
                        if resp.status == 404:
                            await self.register()
                except Exception as e:
                    log.debug("heartbeat failed: %s", e)
        except asyncio.CancelledError:
            pass

    async def _self_update_loop(self) -> None:
        try:
            while not self._stopped:
                # Long jitter to avoid coordinated DoS on X
                base = max(1800, config.SELF_UPDATE_INTERVAL)
                await asyncio.sleep(base + random.uniform(0, base * 0.2))
                if self._stopped:
                    break
                try:
                    async with self.session.get(
                        f"{self.x_base_url}/api/version/c",
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as resp:
                        if resp.status == 200:
                            body = await resp.json()
                            log.debug("latest C version: %s (current %s)", body.get("version"), self.version)
                except Exception as e:
                    log.debug("self_update probe failed: %s", e)
        except asyncio.CancelledError:
            pass

    # ── cache ────────────────────────────────────────────────────────────────
    def _save_cached(self) -> None:
        try:
            os.makedirs(config.CACHE_DIR, exist_ok=True)
            with open(self._cache_path, "w") as f:
                json.dump({
                    "group_id": self.group_id,
                    "tunnel_secret": self.tunnel_secret,
                    "relay_addr": self.relay_addr,
                    "relay_port": self.relay_port,
                }, f)
        except OSError:
            pass

    def _load_cached(self) -> None:
        try:
            with open(self._cache_path) as f:
                data = json.load(f)
            if data.get("group_id") != self.group_id:
                return
            self.tunnel_secret = data.get("tunnel_secret") or self.tunnel_secret
            self.relay_addr = data.get("relay_addr") or self.relay_addr
            self.relay_port = data.get("relay_port") or self.relay_port
            if self.relay_addr and self.relay_port:
                config.RELAY_ADDR = self.relay_addr
                config.RELAY_PORT = self.relay_port
            if self.tunnel_secret:
                config.TUNNEL_SECRET = self.tunnel_secret
            log.info("Loaded cached X config for %s", self.group_id)
        except (OSError, json.JSONDecodeError):
            pass
