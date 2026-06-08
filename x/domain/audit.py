from __future__ import annotations
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class AuditEvent:
    ts: int
    group_id: str
    method: str
    path: str
    status: int
    latency_ms: int
    b_client_id: Optional[str] = None
    upstream_status: Optional[int] = None
    error_type: Optional[str] = None

    def __post_init__(self):
        if self.latency_ms < 0:
            raise ValueError("latency_ms must be >= 0")
        if not self.method:
            raise ValueError("method required")
