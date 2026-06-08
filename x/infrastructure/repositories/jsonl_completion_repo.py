from __future__ import annotations

import dataclasses
import json
import os
from datetime import datetime, timezone

from x.domain.completion import CompletionRecord
from x.infrastructure.repositories.base import CompletionRepository


class JsonlCompletionRepository(CompletionRepository):
    def __init__(self, data_dir: str):
        self._data_dir = data_dir

    def append(self, record: CompletionRecord) -> None:
        os.makedirs(self._data_dir, exist_ok=True)
        date_str = datetime.fromtimestamp(record.ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        path = os.path.join(self._data_dir, f"{date_str}.jsonl")
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(dataclasses.asdict(record), ensure_ascii=False) + "\n")
