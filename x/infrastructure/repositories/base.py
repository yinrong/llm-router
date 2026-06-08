from __future__ import annotations
from abc import ABC, abstractmethod
from typing import List, Optional

from x.domain.group import Group
from x.domain.client import Client, ClientRole
from x.domain.audit import AuditEvent
from x.domain.election import CandidateSnapshot


class GroupRepository(ABC):
    @abstractmethod
    def save(self, group: Group) -> None: ...

    @abstractmethod
    def get(self, group_id: str) -> Optional[Group]: ...

    @abstractmethod
    def get_by_secret(self, tunnel_secret: str) -> Optional[Group]: ...

    @abstractmethod
    def list_by_phone(self, phone: Optional[str] = None) -> List[Group]: ...

    @abstractmethod
    def update_b_addr(self, group_id: str, addr: str, port: int, ts: int) -> None: ...


class ClientRepository(ABC):
    @abstractmethod
    def upsert(self, client: Client) -> Client: ...

    @abstractmethod
    def get(self, client_id: str) -> Optional[Client]: ...

    @abstractmethod
    def list_by_group(
        self, group_id: str, role: Optional[ClientRole] = None
    ) -> List[Client]: ...

    @abstractmethod
    def update_heartbeat(self, client_id: str, ts: int) -> bool: ...

    @abstractmethod
    def set_active(self, group_id: str, winner_id: str, winner_since: int) -> None: ...

    @abstractmethod
    def clear_active(self, group_id: str) -> None: ...

    @abstractmethod
    def get_candidates(self, group_id: str) -> List[CandidateSnapshot]: ...


class AuditRepository(ABC):
    @abstractmethod
    def insert_many(self, events: List[AuditEvent]) -> int: ...


from x.domain.completion import CompletionRecord  # noqa: E402


class CompletionRepository(ABC):
    @abstractmethod
    def append(self, record: CompletionRecord) -> None: ...
