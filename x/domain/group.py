from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Optional

_GROUP_ID_RE = re.compile(r"^(\d{11})_([A-Za-z0-9_-]{1,32})$")


@dataclass(frozen=True)
class GroupId:
    value: str

    def __post_init__(self):
        if not _GROUP_ID_RE.match(self.value):
            raise ValueError(
                f"group_id must match {{11-digit phone}}_{{1-32 [A-Za-z0-9_-]}}, got: {self.value!r}"
            )

    @classmethod
    def of(cls, phone: str, suffix: str) -> "GroupId":
        return cls(f"{phone}_{suffix}")

    @property
    def phone(self) -> str:
        return self.value.split("_", 1)[0]

    @property
    def suffix(self) -> str:
        return self.value.split("_", 1)[1]


@dataclass(frozen=True)
class Group:
    group_id: GroupId
    phone: str
    suffix: str
    tunnel_secret: str
    created_at: int
    b_addr: Optional[str] = None
    b_port: Optional[int] = None
    b_last_seen: Optional[int] = None
