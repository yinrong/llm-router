import pytest
from x.domain.audit import AuditEvent


def test_audit_event_basic():
    e = AuditEvent(
        ts=1000, group_id="13800000001_x",
        method="POST", path="/v1/chat/completions",
        status=200, latency_ms=42,
    )
    assert e.latency_ms == 42
    assert e.b_client_id is None


def test_audit_event_negative_latency():
    with pytest.raises(ValueError, match="latency_ms"):
        AuditEvent(ts=1000, group_id="g", method="GET", path="/", status=200, latency_ms=-1)


def test_audit_event_empty_method():
    with pytest.raises(ValueError, match="method"):
        AuditEvent(ts=1000, group_id="g", method="", path="/", status=200, latency_ms=0)


def test_audit_event_frozen():
    e = AuditEvent(ts=1000, group_id="g", method="GET", path="/", status=200, latency_ms=0)
    with pytest.raises(Exception):
        e.status = 500


def test_audit_event_with_optional_fields():
    e = AuditEvent(
        ts=1000, group_id="g", method="POST", path="/v1/chat",
        status=502, latency_ms=10,
        b_client_id="b-1", upstream_status=503, error_type="upstream_error",
    )
    assert e.b_client_id == "b-1"
    assert e.upstream_status == 503
    assert e.error_type == "upstream_error"
