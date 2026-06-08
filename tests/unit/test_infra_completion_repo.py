import json
import os
import tempfile
import pytest
from x.domain.completion import CompletionRecord
from x.infrastructure.repositories.jsonl_completion_repo import JsonlCompletionRepository


def make_record(**kwargs):
    defaults = dict(
        ts=1749340000000,
        group_id="13800000001_home",
        model="claude-opus-4-8",
        messages=[{"role": "user", "content": "hello"}],
        response_content="hi there",
        finish_reason="end_turn",
        usage={"input_tokens": 10, "output_tokens": 5},
        latency_ms=123,
    )
    defaults.update(kwargs)
    return CompletionRecord(**defaults)


def test_append_creates_file_and_writes_json():
    with tempfile.TemporaryDirectory() as tmpdir:
        repo = JsonlCompletionRepository(tmpdir)
        r = make_record()
        repo.append(r)

        files = os.listdir(tmpdir)
        assert len(files) == 1
        assert files[0].endswith(".jsonl")

        with open(os.path.join(tmpdir, files[0])) as f:
            line = json.loads(f.readline())

        assert line["group_id"] == "13800000001_home"
        assert line["model"] == "claude-opus-4-8"
        assert line["messages"] == [{"role": "user", "content": "hello"}]
        assert line["response_content"] == "hi there"
        assert line["finish_reason"] == "end_turn"
        assert line["usage"] == {"input_tokens": 10, "output_tokens": 5}
        assert line["latency_ms"] == 123
        assert line["ts"] == 1749340000000


def test_append_multiple_records_same_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        repo = JsonlCompletionRepository(tmpdir)
        repo.append(make_record(ts=1000))
        repo.append(make_record(ts=2000))

        files = os.listdir(tmpdir)
        assert len(files) == 1

        with open(os.path.join(tmpdir, files[0])) as f:
            lines = f.readlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["ts"] == 1000
        assert json.loads(lines[1])["ts"] == 2000


def test_append_creates_dir_if_missing():
    with tempfile.TemporaryDirectory() as tmpdir:
        subdir = os.path.join(tmpdir, "nested", "completions")
        repo = JsonlCompletionRepository(subdir)
        repo.append(make_record())
        assert os.path.isdir(subdir)


def test_filename_is_date_based():
    with tempfile.TemporaryDirectory() as tmpdir:
        repo = JsonlCompletionRepository(tmpdir)
        # ts = 2025-06-08 00:00:00 UTC → 2025-06-08.jsonl
        repo.append(make_record(ts=1749340800000))
        files = os.listdir(tmpdir)
        assert files[0] == "2025-06-08.jsonl"
