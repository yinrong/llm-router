from __future__ import annotations
import secrets
import sqlite3
import time
from typing import List, Optional

from x.domain.group import Group, GroupId
from x.infrastructure.repositories.base import GroupRepository


class SqliteGroupRepository(GroupRepository):
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def save(self, group: Group) -> None:
        gid = group.group_id.value
        cur = self._conn.execute("SELECT group_id FROM groups WHERE group_id=?", (gid,))
        if cur.fetchone():
            raise ValueError(f"group already exists: {gid}")
        self._conn.execute(
            "INSERT INTO groups (group_id, phone, suffix, tunnel_secret, created_at) VALUES (?, ?, ?, ?, ?)",
            (gid, group.phone, group.suffix, group.tunnel_secret, group.created_at),
        )

    def get(self, group_id: str) -> Optional[Group]:
        row = self._conn.execute("SELECT * FROM groups WHERE group_id=?", (group_id,)).fetchone()
        return self._row_to_group(row) if row else None

    def get_by_secret(self, tunnel_secret: str) -> Optional[Group]:
        row = self._conn.execute(
            "SELECT * FROM groups WHERE tunnel_secret=?", (tunnel_secret,)
        ).fetchone()
        return self._row_to_group(row) if row else None

    def list_by_phone(self, phone: Optional[str] = None) -> List[Group]:
        if phone:
            rows = self._conn.execute(
                "SELECT * FROM groups WHERE phone=? ORDER BY created_at", (phone,)
            ).fetchall()
        else:
            rows = self._conn.execute("SELECT * FROM groups ORDER BY created_at").fetchall()
        return [self._row_to_group(row) for row in rows]

    def update_b_addr(self, group_id: str, addr: str, port: int, ts: int) -> None:
        self._conn.execute(
            "UPDATE groups SET b_addr=?, b_port=?, b_last_seen=? WHERE group_id=?",
            (addr, port, ts, group_id),
        )

    @staticmethod
    def _row_to_group(row: sqlite3.Row) -> Group:
        return Group(
            group_id=GroupId(row["group_id"]),
            phone=row["phone"],
            suffix=row["suffix"],
            tunnel_secret=row["tunnel_secret"],
            created_at=row["created_at"],
            b_addr=row["b_addr"],
            b_port=row["b_port"],
            b_last_seen=row["b_last_seen"],
        )
