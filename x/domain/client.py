from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class ClientRole(str, Enum):
    A = "A"
    B = "B"
    C = "C"


@dataclass(frozen=True)
class Client:
    client_id: str
    group_id: str
    role: ClientRole
    hostname: Optional[str]
    version: Optional[str]
    registered_at: int
    last_heartbeat: int
    is_active: bool = False
    active_since: Optional[int] = None
