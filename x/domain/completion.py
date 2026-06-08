from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, List


@dataclass(frozen=True)
class CompletionRecord:
    ts: int
    group_id: str
    model: str
    messages: List[Dict[str, Any]]
    response_content: str
    finish_reason: str
    usage: Dict[str, Any]
    latency_ms: int

    def __post_init__(self):
        if self.latency_ms < 0:
            raise ValueError("latency_ms must be >= 0")
        if not self.group_id:
            raise ValueError("group_id required")
        if not self.model:
            raise ValueError("model required")
