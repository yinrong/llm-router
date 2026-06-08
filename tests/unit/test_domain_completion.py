import pytest
from x.domain.completion import CompletionRecord


def test_completion_record_basic():
    r = CompletionRecord(
        ts=1000,
        group_id="13800000001_home",
        model="claude-opus-4-8",
        messages=[{"role": "user", "content": "hello"}],
        response_content="hi",
        finish_reason="end_turn",
        usage={"input_tokens": 10, "output_tokens": 5},
        latency_ms=123,
    )
    assert r.group_id == "13800000001_home"
    assert r.latency_ms == 123


def test_completion_record_frozen():
    r = CompletionRecord(
        ts=1000, group_id="g", model="m",
        messages=[], response_content="x",
        finish_reason="end_turn", usage={}, latency_ms=0,
    )
    with pytest.raises(Exception):
        r.model = "other"


def test_completion_record_negative_latency():
    with pytest.raises(ValueError, match="latency_ms"):
        CompletionRecord(
            ts=1000, group_id="g", model="m",
            messages=[], response_content="x",
            finish_reason="end_turn", usage={}, latency_ms=-1,
        )


def test_completion_record_empty_group_id():
    with pytest.raises(ValueError, match="group_id"):
        CompletionRecord(
            ts=1000, group_id="", model="m",
            messages=[], response_content="x",
            finish_reason="end_turn", usage={}, latency_ms=0,
        )


def test_completion_record_empty_model():
    with pytest.raises(ValueError, match="model"):
        CompletionRecord(
            ts=1000, group_id="g", model="",
            messages=[], response_content="x",
            finish_reason="end_turn", usage={}, latency_ms=0,
        )
