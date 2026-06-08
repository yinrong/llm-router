"""SQLite connection factory for X.

Only connect() and init_schema() remain here.
All query logic lives in x/infrastructure/repositories/sqlite_*.py.
"""

from __future__ import annotations

import sqlite3
import time


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
        CREATE INDEX IF NOT EXISTS idx_groups_secret ON groups(tunnel_secret);

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
