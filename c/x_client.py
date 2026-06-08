"""C's client for the X coordinator.

Responsibilities:
- Register C with X on startup. Returns updated CSettings (immutable).
- Election polling: sets/clears should_be_active event.
- Heartbeat (independent of election poll).
- Self-update placeholder.

No global variable mutation — tunnel_secret changes return a new CSettings.
"""

from __future__ import annotations

import asyncio
import logging
import random
import socket
from typing import Optional

import aiohttp

from c import cache as ccache
from c.settings import CSettings

log = logging.getLogger("c.x")


class XClient:
    def __init__(self, settings: CSettings):
        self._settings = settings
        self.session: Optional[aiohttp.ClientSession] = None
        self.should_be_active = asyncio.Event()
        self._tasks: list[asyncio.Task] = []
        self._stopped = False

    @property
    def settings(self) -> CSettings:
        return self._settings

    async def start(self) -> CSettings:
        """Start background tasks, return updated settings (possibly new tunnel_secret)."""
        if self.session is None:
            self.session = aiohttp.ClientSession()
        new_settings = await self.register()
        if new_settings:
            self._settings = new_settings
        else:
            cached = ccache.load(self._settings.cache_dir, self._settings.group_id)
            if cached:
                self._settings = self._settings.with_tunnel_secret(cached)
                log.info("Loaded cached tunnel_secret for %s", self._settings.group_id)
        self._tasks.append(asyncio.create_task(self._election_loop()))
        self._tasks.append(asyncio.create_task(self._heartbeat_loop()))
        self._tasks.append(asyncio.create_task(self._self_update_loop()))
        return self._settings

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

    async def register(self) -> Optional[CSettings]:
        if not self._settings.group_id:
            log.warning("GROUP_ID not configured, skipping X registration")
            return None
        try:
            async with self.session.post(
                f"{self._settings.x_base_url}/api/register/c",
                json={
                    "group_id": self._settings.group_id,
                    "client_id": self._settings.client_id,
                    "version": self._settings.version,
                    "hostname": socket.gethostname(),
                },
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    log.warning("X register/c returned %s", resp.status)
                    return None
                body = await resp.json()
        except Exception as e:
            log.warning("X register/c failed: %s", e)
            return None

        new_secret = body.get("tunnel_secret") or self._settings.tunnel_secret
        new_settings = self._settings.with_tunnel_secret(new_secret)
        ccache.save(new_settings.cache_dir, new_settings.group_id, new_secret)
        log.info("Registered with X (group=%s)", self._settings.group_id)
        return new_settings

    async def _election_loop(self) -> None:
        try:
            while not self._stopped:
                await self._claim_active_once()
                jitter = random.uniform(0, 0.3)
                await asyncio.sleep(max(1, self._settings.election_poll_interval) + jitter)
        except asyncio.CancelledError:
            pass

    async def _claim_active_once(self) -> None:
        if not self._settings.group_id:
            self.should_be_active.set()
            return
        try:
            async with self.session.post(
                f"{self._settings.x_base_url}/api/elect/{self._settings.group_id}",
                json={"client_id": self._settings.client_id},
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status == 404:
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
                log.info("Elected ACTIVE (group=%s)", self._settings.group_id)
            self.should_be_active.set()
        else:
            if self.should_be_active.is_set():
                log.info("Lost active to %s; standing by", body.get("active_client_id"))
            self.should_be_active.clear()

    async def _heartbeat_loop(self) -> None:
        try:
            while not self._stopped:
                await asyncio.sleep(self._settings.heartbeat_interval)
                if self._stopped:
                    break
                try:
                    async with self.session.post(
                        f"{self._settings.x_base_url}/api/heartbeat",
                        json={
                            "client_id": self._settings.client_id,
                            "role": "C",
                            "group_id": self._settings.group_id,
                        },
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
                base = max(1800, self._settings.self_update_interval)
                await asyncio.sleep(base + random.uniform(0, base * 0.2))
                if self._stopped:
                    break
                try:
                    async with self.session.get(
                        f"{self._settings.x_base_url}/api/version/c",
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as resp:
                        if resp.status == 200:
                            body = await resp.json()
                            log.debug(
                                "latest C version: %s (current %s)",
                                body.get("version"), self._settings.version,
                            )
                except Exception as e:
                    log.debug("self_update probe failed: %s", e)
        except asyncio.CancelledError:
            pass
