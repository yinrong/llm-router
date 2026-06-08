"""Cache helpers for C's tunnel_secret (fail-open design)."""

from __future__ import annotations

import json
import logging
import os
from typing import Optional

log = logging.getLogger("c.cache")


def save(cache_dir: str, group_id: str, tunnel_secret: str) -> None:
    try:
        os.makedirs(cache_dir, exist_ok=True)
        path = os.path.join(cache_dir, "c-tunnel.json")
        with open(path, "w") as f:
            json.dump({"group_id": group_id, "tunnel_secret": tunnel_secret}, f)
    except OSError as e:
        log.debug("Failed to cache tunnel_secret: %s", e)


def load(cache_dir: str, group_id: str) -> Optional[str]:
    try:
        path = os.path.join(cache_dir, "c-tunnel.json")
        with open(path) as f:
            data = json.load(f)
        if data.get("group_id") != group_id:
            return None
        return data.get("tunnel_secret") or None
    except (OSError, json.JSONDecodeError):
        return None
