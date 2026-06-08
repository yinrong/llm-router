from __future__ import annotations
import time
from typing import List, Optional

from x.domain.audit import AuditEvent
from x.infrastructure.repositories.base import AuditRepository, GroupRepository


class AuditService:
    def __init__(self, audit_repo: AuditRepository, group_repo: GroupRepository):
        self._audits = audit_repo
        self._groups = group_repo

    def post_events(
        self,
        group_id: str,
        b_client_id: Optional[str],
        raw_events: List[dict],
    ) -> int:
        if not self._groups.get(group_id):
            raise KeyError(f"unknown group_id: {group_id}")
        events = [self._parse_event(group_id, b_client_id, e) for e in raw_events]
        return self._audits.insert_many(events)

    def record_relay_event(
        self,
        group_id: str,
        method: str,
        path: str,
        status: int,
        started_at: float,
        *,
        error_type: Optional[str] = None,
        upstream_status: Optional[int] = None,
        b_client_id: Optional[str] = None,
    ) -> None:
        if not group_id:
            return
        try:
            latency_ms = max(0, int((time.time() - started_at) * 1000))
            event = AuditEvent(
                ts=int(time.time()),
                group_id=group_id,
                method=method,
                path=path,
                status=status,
                latency_ms=latency_ms,
                b_client_id=b_client_id,
                upstream_status=upstream_status,
                error_type=error_type,
            )
            self._audits.insert_many([event])
        except Exception:
            pass  # audit 失败不阻断主流程

    @staticmethod
    def _parse_event(
        group_id: str, b_client_id: Optional[str], e: dict
    ) -> AuditEvent:
        return AuditEvent(
            ts=int(e.get("ts", time.time())),
            group_id=group_id,
            b_client_id=b_client_id,
            method=e.get("method", ""),
            path=e.get("path", ""),
            status=int(e.get("status", 0)),
            latency_ms=int(e.get("latency_ms", 0)),
            upstream_status=(
                int(e["upstream_status"])
                if e.get("upstream_status") is not None
                else None
            ),
            error_type=e.get("error_type"),
        )
