"""SQLite layer for X. Pure functions; HTTP layer in x/server.py."""

from __future__ import annotations

import re
import secrets
import sqlite3
import time
from typing import Iterable

GROUP_ID_RE = re.compile(r"^(\d{11})_([A-Za-z0-9_-]{1,32})$")

ACTIVE_TAKEOVER_FACTOR = 2  # active is stale after 2 * election_poll seconds


def now_ts() -> int:
    return int(time.time())


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    init_schema(conn)
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS groups (
          group_id TEXT PRIMARY KEY,
          phone TEXT NOT NULL,
          suffix TEXT NOT NULL,
          tunnel_secret TEXT NOT NULL,
          created_at INTEGER NOT NULL,
          b_addr TEXT,
          b_port INTEGER,
          b_last_seen INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_groups_phone ON groups(phone);

        CREATE TABLE IF NOT EXISTS clients (
          client_id TEXT PRIMARY KEY,
          group_id TEXT NOT NULL,
          role TEXT NOT NULL CHECK (role IN ('B','C','A')),
          hostname TEXT,
          version TEXT,
          registered_at INTEGER NOT NULL,
          last_heartbeat INTEGER NOT NULL,
          is_active INTEGER NOT NULL DEFAULT 0,
          active_since INTEGER,
          FOREIGN KEY (group_id) REFERENCES groups(group_id)
        );
        CREATE INDEX IF NOT EXISTS idx_clients_group ON clients(group_id, role);
        CREATE INDEX IF NOT EXISTS idx_clients_heartbeat ON clients(last_heartbeat);

        CREATE TABLE IF NOT EXISTS audit_log (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          ts INTEGER NOT NULL,
          group_id TEXT NOT NULL,
          b_client_id TEXT,
          method TEXT,
          path TEXT,
          status INTEGER,
          latency_ms INTEGER,
          upstream_status INTEGER,
          error_type TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_audit_group_ts ON audit_log(group_id, ts);
        """
    )


def validate_group_id(group_id: str) -> tuple[str, str]:
    """Return (phone, suffix). Raises ValueError on bad input."""
    if not isinstance(group_id, str):
        raise ValueError("group_id must be a string")
    m = GROUP_ID_RE.match(group_id)
    if not m:
        raise ValueError("group_id must match {phone(11 digits)}_{suffix(1-32 [A-Za-z0-9_-])}")
    return m.group(1), m.group(2)


def create_group(conn: sqlite3.Connection, phone: str, suffix: str, *, tunnel_secret: str | None = None, ts: int | None = None) -> dict:
    group_id = f"{phone}_{suffix}"
    validate_group_id(group_id)
    if tunnel_secret is None:
        tunnel_secret = "tun-" + secrets.token_urlsafe(32)
    if ts is None:
        ts = now_ts()
    cur = conn.execute("SELECT group_id FROM groups WHERE group_id=?", (group_id,))
    if cur.fetchone():
        raise ValueError("group already exists")
    conn.execute(
        "INSERT INTO groups (group_id, phone, suffix, tunnel_secret, created_at) VALUES (?, ?, ?, ?, ?)",
        (group_id, phone, suffix, tunnel_secret, ts),
    )
    return {"group_id": group_id, "tunnel_secret": tunnel_secret, "created_at": ts}


def get_group(conn: sqlite3.Connection, group_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM groups WHERE group_id=?", (group_id,)).fetchone()
    return dict(row) if row else None


def list_groups_by_phone(conn: sqlite3.Connection, phone: str | None = None) -> list[dict]:
    if phone:
        rows = conn.execute("SELECT * FROM groups WHERE phone=? ORDER BY created_at", (phone,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM groups ORDER BY created_at").fetchall()
    out = []
    for row in rows:
        d = dict(row)
        d["c_active_count"] = conn.execute(
            "SELECT COUNT(*) FROM clients WHERE group_id=? AND role='C' AND is_active=1",
            (d["group_id"],),
        ).fetchone()[0]
        d["c_total_count"] = conn.execute(
            "SELECT COUNT(*) FROM clients WHERE group_id=? AND role='C'",
            (d["group_id"],),
        ).fetchone()[0]
        out.append(d)
    return out


def update_b_addr(conn: sqlite3.Connection, group_id: str, addr: str, port: int, *, ts: int | None = None) -> None:
    if ts is None:
        ts = now_ts()
    conn.execute(
        "UPDATE groups SET b_addr=?, b_port=?, b_last_seen=? WHERE group_id=?",
        (addr, port, ts, group_id),
    )


def upsert_client(
    conn: sqlite3.Connection,
    *,
    client_id: str,
    group_id: str,
    role: str,
    hostname: str | None = None,
    version: str | None = None,
    ts: int | None = None,
) -> dict:
    if role not in ("A", "B", "C"):
        raise ValueError("role must be A/B/C")
    if not get_group(conn, group_id):
        raise ValueError("unknown group_id")
    if ts is None:
        ts = now_ts()
    existing = conn.execute("SELECT * FROM clients WHERE client_id=?", (client_id,)).fetchone()
    if existing:
        conn.execute(
            "UPDATE clients SET group_id=?, role=?, hostname=?, version=?, last_heartbeat=? WHERE client_id=?",
            (group_id, role, hostname, version, ts, client_id),
        )
    else:
        conn.execute(
            "INSERT INTO clients (client_id, group_id, role, hostname, version, registered_at, last_heartbeat) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (client_id, group_id, role, hostname, version, ts, ts),
        )
    row = conn.execute("SELECT * FROM clients WHERE client_id=?", (client_id,)).fetchone()
    return dict(row)


def heartbeat(conn: sqlite3.Connection, client_id: str, *, ts: int | None = None) -> bool:
    if ts is None:
        ts = now_ts()
    cur = conn.execute("UPDATE clients SET last_heartbeat=? WHERE client_id=?", (ts, client_id))
    return cur.rowcount > 0


def insert_audit_events(conn: sqlite3.Connection, group_id: str, b_client_id: str | None, events: Iterable[dict]) -> int:
    rows = []
    for e in events:
        rows.append((
            int(e.get("ts", now_ts())),
            group_id,
            b_client_id,
            e.get("method"),
            e.get("path"),
            int(e["status"]) if e.get("status") is not None else None,
            int(e["latency_ms"]) if e.get("latency_ms") is not None else None,
            int(e["upstream_status"]) if e.get("upstream_status") is not None else None,
            e.get("error_type"),
        ))
    if not rows:
        return 0
    conn.executemany(
        "INSERT INTO audit_log (ts, group_id, b_client_id, method, path, status, latency_ms, upstream_status, error_type) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    return len(rows)
