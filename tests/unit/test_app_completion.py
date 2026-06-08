import json
import os
import tempfile
import pytest
from x.application.completion_service import CompletionService
from x.infrastructure.repositories.jsonl_completion_repo import JsonlCompletionRepository


def make_raw():
    return {
        "ts": 1749340000000,
        "group_id": "13800000001_home",
        "model": "claude-opus-4-8",
        "messages": [{"role": "user", "content": "hello"}],
        "response_content": "hi",
        "finish_reason": "end_turn",
        "usage": {"input_tokens": 10, "output_tokens": 5},
        "latency_ms": 123,
    }


def test_record_writes_to_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        repo = JsonlCompletionRepository(tmpdir)
        svc = CompletionService(repo)
        svc.record(make_raw())

        files = os.listdir(tmpdir)
        assert len(files) == 1
        with open(os.path.join(tmpdir, files[0])) as f:
            line = json.loads(f.readline())
        assert line["group_id"] == "13800000001_home"


def test_record_exception_is_silenced():
    class BrokenRepo:
        def append(self, record):
            raise RuntimeError("disk full")

    svc = CompletionService(BrokenRepo())
    svc.record(make_raw())  # 不抛出


def test_record_missing_field_is_silenced():
    with tempfile.TemporaryDirectory() as tmpdir:
        repo = JsonlCompletionRepository(tmpdir)
        svc = CompletionService(repo)
        bad = make_raw()
        del bad["model"]
        svc.record(bad)  # 字段缺失不抛出，只静默丢弃


def test_record_empty_group_id_is_silenced():
    with tempfile.TemporaryDirectory() as tmpdir:
        repo = JsonlCompletionRepository(tmpdir)
        svc = CompletionService(repo)
        raw = make_raw()
        raw["group_id"] = ""
        svc.record(raw)  # 空 group_id 不抛出，只静默丢弃
        assert os.listdir(tmpdir) == []
