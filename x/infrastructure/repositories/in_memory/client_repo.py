from __future__ import annotations
import threading
from dataclasses import replace
from typing import Dict, List, Optional

from x.domain.client import Client, ClientRole
from x.domain.election import CandidateSnapshot
from x.infrastructure.repositories.base import ClientRepository


class InMemoryClientRepository(ClientRepository):
    def __init__(self):
        self._store: Dict[str, Client] = {}
        self._lock = threading.Lock()

    def upsert(self, client: Client) -> Client:
        with self._lock:
            existing = self._store.get(client.client_id)
            if existing:
                # 更新 version/hostname/last_heartbeat，保留 is_active/active_since
                client = replace(
                    existing,
                    version=client.version,
                    hostname=client.hostname,
                    last_heartbeat=client.last_heartbeat,
                )
            self._store[client.client_id] = client
            return client

    def get(self, client_id: str) -> Optional[Client]:
        return self._store.get(client_id)

    def list_by_group(
        self, group_id: str, role: Optional[ClientRole] = None
    ) -> List[Client]:
        clients = [c for c in self._store.values() if c.group_id == group_id]
        if role:
            clients = [c for c in clients if c.role == role]
        return clients

    def update_heartbeat(self, client_id: str, ts: int) -> bool:
        with self._lock:
            c = self._store.get(client_id)
            if c is None:
                return False
            self._store[client_id] = replace(c, last_heartbeat=ts)
            return True

    def set_active(self, group_id: str, winner_id: str, winner_since: int) -> None:
        with self._lock:
            c = self._store.get(winner_id)
            if c and c.group_id == group_id:
                self._store[winner_id] = replace(c, is_active=True, active_since=winner_since)

    def clear_active(self, group_id: str) -> None:
        with self._lock:
            for cid, c in list(self._store.items()):
                if c.group_id == group_id and c.is_active:
                    self._store[cid] = replace(c, is_active=False, active_since=None)

    def get_candidates(self, group_id: str) -> List[CandidateSnapshot]:
        return [
            CandidateSnapshot(
                client_id=c.client_id,
                last_heartbeat=c.last_heartbeat,
                registered_at=c.registered_at,
                is_active=c.is_active,
                active_since=c.active_since,
            )
            for c in self._store.values()
            if c.group_id == group_id and c.role == ClientRole.C
        ]
