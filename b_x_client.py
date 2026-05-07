"""B's client for the X coordinator.

Responsibilities:
- Register B with X on startup (POST /api/register/b), pick up the group's
  tunnel_secret, and write it to config.TUNNEL_SECRET.
- Heartbeat to X on a fixed interval.
- Buffer audit events and flush them in batches (POST /api/audit).
- Best-effort: any failure here is logged but never affects the data path.

X is fail-open. If X is unreachable but a cached tunnel_secret exists for
this group_id, we proceed; if env still supplies one, we proceed too.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import time
from typing import Any

import aiohttp

import config

log = logging.getLogger("b.x")


class XClient:
    def __init__(
        self,
        *,
        x_base_url: str,
        group_id: str,
        client_id: str,
        public_addr: str,
        port: int,
        version: str = "0.1.0",
    ):
        self.x_base_url = x_base_url.rstrip("/")
        self.group_id = group_id
        self.client_id = client_id
        self.public_addr = public_addr
        self.port = port
        self.version = version
        self.tunnel_secret: str | None = None
        self.session: aiohttp.ClientSession | None = None
        self.audit_queue: asyncio.Queue = asyncio.Queue(maxsize=config.X_AUDIT_QUEUE_MAX)
        self._tasks: list[asyncio.Task] = []
        self._stopped = False
        self._cache_path = os.path.join(config.CACHE_DIR, "b-tunnel.json")

    # ── lifecycle ────────────────────────────────────────────────────────────
    async def start(self) -> None:
        """Register with X (best effort), then spawn heartbeat + audit tasks."""
        if self.session is None:
            self.session = aiohttp.ClientSession()
        ok = await self.register()
        if not ok:
            self._load_cached_secret()
        self._tasks.append(asyncio.create_task(self._heartbeat_loop()))
        self._tasks.append(asyncio.create_task(self._audit_flush_loop()))

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
                f"{self.x_base_url}/api/register/b",
                json={
                    "group_id": self.group_id,
                    "client_id": self.client_id,
                    "public_addr": self.public_addr,
                    "port": self.port,
                    "version": self.version,
                    "hostname": socket.gethostname(),
                },
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    log.warning("X register/b returned %s", resp.status)
                    return False
                body = await resp.json()
        except Exception as e:
            log.warning("X register/b failed: %s", e)
            return False

        secret = body.get("tunnel_secret")
        if secret:
            self.tunnel_secret = secret
            config.TUNNEL_SECRET = secret  # mutate at runtime; safe (single loop)
            self._save_cached_secret(secret)
            log.info("Registered with X; tunnel_secret obtained (group=%s)", self.group_id)
        return True

    async def _heartbeat_loop(self) -> None:
        try:
            while not self._stopped:
                await asyncio.sleep(config.X_HEARTBEAT_INTERVAL)
                if self._stopped:
                    break
                try:
                    async with self.session.post(
                        f"{self.x_base_url}/api/heartbeat",
                        json={"client_id": self.client_id, "role": "B", "group_id": self.group_id},
                        timeout=aiohttp.ClientTimeout(total=5),
                    ) as resp:
                        if resp.status == 404:
                            await self.register()
                except Exception as e:
                    log.debug("heartbeat failed: %s", e)
        except asyncio.CancelledError:
            pass

    # ── audit ────────────────────────────────────────────────────────────────
    def enqueue_audit(self, event: dict) -> None:
        try:
            self.audit_queue.put_nowait(event)
        except asyncio.QueueFull:
            try:
                _ = self.audit_queue.get_nowait()
                self.audit_queue.put_nowait(event)
            except Exception:
                pass

    async def _audit_flush_loop(self) -> None:
        try:
            while not self._stopped:
                # Wait either for batch interval or for queue to fill enough.
                try:
                    await asyncio.wait_for(self._wait_for_batch(), timeout=config.X_AUDIT_BATCH_INTERVAL)
                except asyncio.TimeoutError:
                    pass
                events: list[dict] = []
                while not self.audit_queue.empty() and len(events) < config.X_AUDIT_BATCH_MAX:
                    try:
                        events.append(self.audit_queue.get_nowait())
                    except asyncio.QueueEmpty:
                        break
                if not events:
                    continue
                await self._flush(events)
        except asyncio.CancelledError:
            # Flush remaining on cancel — best effort.
            if not self.audit_queue.empty():
                events = []
                while not self.audit_queue.empty() and len(events) < config.X_AUDIT_BATCH_MAX:
                    try:
                        events.append(self.audit_queue.get_nowait())
                    except asyncio.QueueEmpty:
                        break
                if events:
                    try:
                        await self._flush(events)
                    except Exception:
                        pass

    async def _wait_for_batch(self):
        # Wait until queue reaches threshold; the timeout in caller bounds this.
        while not self._stopped and self.audit_queue.qsize() < config.X_AUDIT_BATCH_MAX:
            await asyncio.sleep(0.05)

    async def _flush(self, events: list[dict]) -> None:
        if not self.group_id:
            return
        try:
            async with self.session.post(
                f"{self.x_base_url}/api/audit",
                json={
                    "group_id": self.group_id,
                    "b_client_id": self.client_id,
                    "events": events,
                },
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status >= 400:
                    log.debug("audit flush returned %s", resp.status)
        except Exception as e:
            log.debug("audit flush failed: %s", e)

    # ── cache ────────────────────────────────────────────────────────────────
    def _save_cached_secret(self, secret: str) -> None:
        try:
            os.makedirs(config.CACHE_DIR, exist_ok=True)
            with open(self._cache_path, "w") as f:
                json.dump({"group_id": self.group_id, "tunnel_secret": secret}, f)
        except OSError:
            pass

    def _load_cached_secret(self) -> None:
        try:
            with open(self._cache_path) as f:
                data = json.load(f)
            if data.get("group_id") == self.group_id and data.get("tunnel_secret"):
                self.tunnel_secret = data["tunnel_secret"]
                config.TUNNEL_SECRET = data["tunnel_secret"]
                log.info("Using cached tunnel_secret for %s", self.group_id)
        except (OSError, json.JSONDecodeError):
            pass


def make_audit_event(*, method: str, path: str, status: int, started_at: float, error_type: str | None = None, upstream_status: int | None = None) -> dict:
    latency_ms = max(0, int((time.time() - started_at) * 1000))
    return {
        "ts": int(time.time()),
        "method": method,
        "path": path,
        "status": status,
        "latency_ms": latency_ms,
        "upstream_status": upstream_status,
        "error_type": error_type,
    }
