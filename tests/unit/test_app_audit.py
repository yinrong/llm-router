import time
import pytest
from x.application.group_service import GroupService
from x.application.audit_service import AuditService
from x.infrastructure.repositories.in_memory.group_repo import InMemoryGroupRepository
from x.infrastructure.repositories.in_memory.audit_repo import InMemoryAuditRepository


def setup():
    group_repo = InMemoryGroupRepository()
    audit_repo = InMemoryAuditRepository()
    group_svc = GroupService(group_repo)
    audit_svc = AuditService(audit_repo, group_repo)
    return group_svc, audit_svc, audit_repo


GID = "13800000001_audit"


def test_post_events_stores_records():
    group_svc, audit_svc, audit_repo = setup()
    group_svc.create_group("13800000001", "audit")
    n = audit_svc.post_events(GID, "b-1", [
        {"method": "POST", "path": "/v1/chat", "status": 200, "latency_ms": 42, "ts": 1000},
        {"method": "GET", "path": "/v1/models", "status": 200, "latency_ms": 5, "ts": 1001},
    ])
    assert n == 2
    events = audit_repo.all()
    assert len(events) == 2
    assert events[0].method == "POST"
    assert events[0].b_client_id == "b-1"


def test_post_events_unknown_group_raises():
    _, audit_svc, _ = setup()
    with pytest.raises(KeyError):
        audit_svc.post_events("13800000002_ghost", None, [
            {"method": "GET", "path": "/", "status": 200, "latency_ms": 0, "ts": 1000},
        ])


def test_record_relay_event():
    group_svc, audit_svc, audit_repo = setup()
    group_svc.create_group("13800000001", "audit")
    started = time.time() - 0.05  # 50ms ago
    audit_svc.record_relay_event(GID, "POST", "/v1/chat", 200, started)
    events = audit_repo.all()
    assert len(events) == 1
    assert events[0].status == 200
    assert events[0].latency_ms >= 0


def test_record_relay_event_empty_group_is_noop():
    _, audit_svc, audit_repo = setup()
    audit_svc.record_relay_event("", "POST", "/v1/chat", 200, time.time())
    assert audit_repo.all() == []


def test_record_relay_event_exception_is_silenced():
    # 即使 repo 抛出，也不应向外传播
    group_repo = InMemoryGroupRepository()

    class BrokenAuditRepo:
        def insert_many(self, events):
            raise RuntimeError("db down")

    audit_svc = AuditService(BrokenAuditRepo(), group_repo)
    audit_svc.record_relay_event("some-group", "POST", "/", 200, time.time())  # 不抛出
