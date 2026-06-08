"""Immutable C-side configuration value object.

Replaces direct reads from the global config module.
After X registration, use with_tunnel_secret() to return a new instance —
no global variable mutation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse


@dataclass(frozen=True)
class CSettings:
    x_base_url: str
    group_id: str
    client_id: str
    tunnel_secret: str
    internal_llm_base: str
    cache_dir: str
    version: str = "0.1.0"
    election_poll_interval: int = 5
    heartbeat_interval: int = 30
    request_timeout: int = 120
    reconnect_base: int = 5
    reconnect_max: int = 60
    heartbeat_min: int = 20
    heartbeat_max: int = 40
    ws_path: str = "/ws/notifications"
    self_update_interval: int = 1800

    @property
    def ws_url(self) -> str:
        p = urlparse(self.x_base_url)
        scheme = "wss" if p.scheme == "https" else "ws"
        return f"{scheme}://{p.netloc}{self.ws_path}"

    def with_tunnel_secret(self, secret: str) -> "CSettings":
        """Return a new CSettings with an updated tunnel_secret (immutable update)."""
        return CSettings(
            x_base_url=self.x_base_url,
            group_id=self.group_id,
            client_id=self.client_id,
            tunnel_secret=secret,
            internal_llm_base=self.internal_llm_base,
            cache_dir=self.cache_dir,
            version=self.version,
            election_poll_interval=self.election_poll_interval,
            heartbeat_interval=self.heartbeat_interval,
            request_timeout=self.request_timeout,
            reconnect_base=self.reconnect_base,
            reconnect_max=self.reconnect_max,
            heartbeat_min=self.heartbeat_min,
            heartbeat_max=self.heartbeat_max,
            ws_path=self.ws_path,
            self_update_interval=self.self_update_interval,
        )

    @classmethod
    def from_env(cls) -> "CSettings":
        """Construct from environment variables via config module (called once at startup)."""
        import os
        import config  # noqa — only used at process startup
        config.ROLE = "C"
        client_id = (
            os.environ.get("CLIENT_ID_C")
            or config.load_or_create_client_id()
        )
        return cls(
            x_base_url=config.X_BASE_URL,
            group_id=config.GROUP_ID,
            client_id=client_id,
            tunnel_secret=config.TUNNEL_SECRET,
            internal_llm_base=config.INTERNAL_LLM_BASE,
            cache_dir=config.CACHE_DIR,
            version=getattr(config, "VERSION", "0.1.0"),
            election_poll_interval=config.ELECTION_POLL_INTERVAL,
            heartbeat_interval=config.X_HEARTBEAT_INTERVAL,
            request_timeout=config.REQUEST_TIMEOUT,
            reconnect_base=config.RECONNECT_BASE,
            reconnect_max=config.RECONNECT_MAX,
            heartbeat_min=config.HEARTBEAT_MIN,
            heartbeat_max=config.HEARTBEAT_MAX,
            ws_path=config.WS_PATH,
            self_update_interval=config.SELF_UPDATE_INTERVAL,
        )
