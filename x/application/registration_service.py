from __future__ import annotations
import time
from typing import Optional

from x.domain.client import Client, ClientRole
from x.infrastructure.repositories.base import GroupRepository, ClientRepository


class RegistrationService:
    def __init__(self, group_repo: GroupRepository, client_repo: ClientRepository):
        self._groups = group_repo
        self._clients = client_repo

    def register_b(
        self,
        group_id: str,
        client_id: str,
        *,
        public_addr: str = "",
        port: int = 0,
        version: str = "",
        hostname: str = "",
        ts: Optional[int] = None,
    ) -> dict:
        ts = ts or int(time.time())
        group = self._groups.get(group_id)
        if group is None:
            raise KeyError(f"unknown group_id: {group_id}")
        client = Client(
            client_id=client_id, group_id=group_id, role=ClientRole.B,
            hostname=hostname or None, version=version or None,
            registered_at=ts, last_heartbeat=ts,
        )
        self._clients.upsert(client)
        if public_addr and port:
            self._groups.update_b_addr(group_id, public_addr, port, ts)
        return {"tunnel_secret": group.tunnel_secret}

    def register_c(
        self,
        group_id: str,
        client_id: str,
        *,
        version: str = "",
        hostname: str = "",
        ts: Optional[int] = None,
    ) -> dict:
        ts = ts or int(time.time())
        group = self._groups.get(group_id)
        if group is None:
            raise KeyError(f"unknown group_id: {group_id}")
        client = Client(
            client_id=client_id, group_id=group_id, role=ClientRole.C,
            hostname=hostname or None, version=version or None,
            registered_at=ts, last_heartbeat=ts,
        )
        self._clients.upsert(client)
        # 重新读取 group（可能刚被 register_b 更新了 b_addr）
        group = self._groups.get(group_id)
        return {
            "relay_addr": group.b_addr or "",
            "relay_port": group.b_port or 0,
            "tunnel_secret": group.tunnel_secret,
        }
