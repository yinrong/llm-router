from __future__ import annotations
import time
from typing import Optional

from x.infrastructure.repositories.base import ClientRepository


class HeartbeatService:
    def __init__(self, client_repo: ClientRepository):
        self._clients = client_repo

    def heartbeat(self, client_id: str, *, ts: Optional[int] = None) -> bool:
        ts = ts or int(time.time())
        return self._clients.update_heartbeat(client_id, ts)
