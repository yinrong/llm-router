from __future__ import annotations
import threading
from typing import List

from x.domain.audit import AuditEvent
from x.infrastructure.repositories.base import AuditRepository


class InMemoryAuditRepository(AuditRepository):
    def __init__(self):
        self._events: List[AuditEvent] = []
        self._lock = threading.Lock()

    def insert_many(self, events: List[AuditEvent]) -> int:
        with self._lock:
            self._events.extend(events)
            return len(events)

    def all(self) -> List[AuditEvent]:
        return list(self._events)
