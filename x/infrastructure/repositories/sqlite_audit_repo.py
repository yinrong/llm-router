from __future__ import annotations
import sqlite3
from typing import List

from x.domain.audit import AuditEvent
from x.infrastructure.repositories.base import AuditRepository


class SqliteAuditRepository(AuditRepository):
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def insert_many(self, events: List[AuditEvent]) -> int:
        if not events:
            return 0
        rows = [
            (
                e.ts, e.group_id, e.b_client_id,
                e.method, e.path, e.status, e.latency_ms,
                e.upstream_status, e.error_type,
            )
            for e in events
        ]
        self._conn.executemany(
            "INSERT INTO audit_log (ts, group_id, b_client_id, method, path, status, "
            "latency_ms, upstream_status, error_type) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        return len(rows)
