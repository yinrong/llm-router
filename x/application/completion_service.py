from __future__ import annotations

from x.domain.completion import CompletionRecord
from x.infrastructure.repositories.base import CompletionRepository


class CompletionService:
    def __init__(self, repo: CompletionRepository):
        self._repo = repo

    def record(self, raw: dict) -> None:
        try:
            record = CompletionRecord(
                ts=int(raw["ts"]),
                group_id=raw["group_id"],
                model=raw["model"],
                messages=raw["messages"],
                response_content=raw.get("response_content", ""),
                finish_reason=raw.get("finish_reason", ""),
                usage=raw.get("usage", {}),
                latency_ms=int(raw.get("latency_ms", 0)),
            )
            self._repo.append(record)
        except Exception:
            pass  # completion 日志失败不阻断主流程
