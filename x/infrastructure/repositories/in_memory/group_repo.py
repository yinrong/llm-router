from __future__ import annotations
import threading
from typing import Dict, List, Optional

from x.domain.group import Group
from x.infrastructure.repositories.base import GroupRepository


class InMemoryGroupRepository(GroupRepository):
    def __init__(self):
        self._store: Dict[str, Group] = {}
        self._lock = threading.Lock()

    def save(self, group: Group) -> None:
        with self._lock:
            gid = group.group_id.value
            if gid in self._store:
                raise ValueError(f"group already exists: {gid}")
            self._store[gid] = group

    def get(self, group_id: str) -> Optional[Group]:
        return self._store.get(group_id)

    def get_by_secret(self, tunnel_secret: str) -> Optional[Group]:
        return next(
            (g for g in self._store.values() if g.tunnel_secret == tunnel_secret), None
        )

    def list_by_phone(self, phone: Optional[str] = None) -> List[Group]:
        groups = list(self._store.values())
        if phone:
            groups = [g for g in groups if g.phone == phone]
        return sorted(groups, key=lambda g: g.created_at)

    def update_b_addr(self, group_id: str, addr: str, port: int, ts: int) -> None:
        with self._lock:
            g = self._store.get(group_id)
            if g:
                self._store[group_id] = Group(
                    group_id=g.group_id, phone=g.phone, suffix=g.suffix,
                    tunnel_secret=g.tunnel_secret, created_at=g.created_at,
                    b_addr=addr, b_port=port, b_last_seen=ts,
                )
