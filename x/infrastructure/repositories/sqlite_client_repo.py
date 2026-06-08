from __future__ import annotations
import sqlite3
from dataclasses import replace
from typing import List, Optional

from x.domain.client import Client, ClientRole
from x.domain.election import CandidateSnapshot
from x.infrastructure.repositories.base import ClientRepository


class SqliteClientRepository(ClientRepository):
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def upsert(self, client: Client) -> Client:
        existing = self._conn.execute(
            "SELECT * FROM clients WHERE client_id=?", (client.client_id,)
        ).fetchone()
        if existing:
            self._conn.execute(
                "UPDATE clients SET group_id=?, role=?, hostname=?, version=?, last_heartbeat=? WHERE client_id=?",
                (client.group_id, client.role.value, client.hostname, client.version,
                 client.last_heartbeat, client.client_id),
            )
        else:
            self._conn.execute(
                "INSERT INTO clients (client_id, group_id, role, hostname, version, registered_at, last_heartbeat) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (client.client_id, client.group_id, client.role.value,
                 client.hostname, client.version, client.registered_at, client.last_heartbeat),
            )
        row = self._conn.execute(
            "SELECT * FROM clients WHERE client_id=?", (client.client_id,)
        ).fetchone()
        return self._row_to_client(row)

    def get(self, client_id: str) -> Optional[Client]:
        row = self._conn.execute(
            "SELECT * FROM clients WHERE client_id=?", (client_id,)
        ).fetchone()
        return self._row_to_client(row) if row else None

    def list_by_group(
        self, group_id: str, role: Optional[ClientRole] = None
    ) -> List[Client]:
        if role:
            rows = self._conn.execute(
                "SELECT * FROM clients WHERE group_id=? AND role=?",
                (group_id, role.value),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM clients WHERE group_id=?", (group_id,)
            ).fetchall()
        return [self._row_to_client(row) for row in rows]

    def update_heartbeat(self, client_id: str, ts: int) -> bool:
        cur = self._conn.execute(
            "UPDATE clients SET last_heartbeat=? WHERE client_id=?", (ts, client_id)
        )
        return cur.rowcount > 0

    def set_active(self, group_id: str, winner_id: str, winner_since: int) -> None:
        self._conn.execute(
            "UPDATE clients SET is_active=1, active_since=? WHERE client_id=? AND group_id=?",
            (winner_since, winner_id, group_id),
        )

    def clear_active(self, group_id: str) -> None:
        self._conn.execute(
            "UPDATE clients SET is_active=0, active_since=NULL WHERE group_id=? AND is_active=1",
            (group_id,),
        )

    def get_candidates(self, group_id: str) -> List[CandidateSnapshot]:
        rows = self._conn.execute(
            "SELECT client_id, last_heartbeat, registered_at, is_active, active_since "
            "FROM clients WHERE group_id=? AND role='C'",
            (group_id,),
        ).fetchall()
        return [
            CandidateSnapshot(
                client_id=row["client_id"],
                last_heartbeat=row["last_heartbeat"],
                registered_at=row["registered_at"],
                is_active=bool(row["is_active"]),
                active_since=row["active_since"],
            )
            for row in rows
        ]

    def force_active_for_test(self, group_id: str, winner_id: str, winner_since: int) -> None:
        """仅供测试使用：强制设置 active 状态，绕过选主逻辑。"""
        self._conn.execute(
            "UPDATE clients SET is_active=0, active_since=NULL WHERE group_id=?", (group_id,)
        )
        self._conn.execute(
            "UPDATE clients SET is_active=1, active_since=? WHERE client_id=?",
            (winner_since, winner_id),
        )

    @staticmethod
    def _row_to_client(row: sqlite3.Row) -> Client:
        return Client(
            client_id=row["client_id"],
            group_id=row["group_id"],
            role=ClientRole(row["role"]),
            hostname=row["hostname"],
            version=row["version"],
            registered_at=row["registered_at"],
            last_heartbeat=row["last_heartbeat"],
            is_active=bool(row["is_active"]),
            active_since=row["active_since"],
        )
