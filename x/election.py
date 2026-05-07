"""Leader election among multiple C clients in the same group.

Pure functions on top of x.db. The HTTP layer wraps `claim_active`.
"""

from __future__ import annotations

import sqlite3

from x.db import ACTIVE_TAKEOVER_FACTOR, now_ts


def claim_active(
    conn: sqlite3.Connection,
    group_id: str,
    client_id: str,
    *,
    election_poll: int = 5,
    ts: int | None = None,
) -> dict:
    """Idempotent claim — also acts as a heartbeat for `client_id`.

    Returns {active: bool, active_client_id: str|None, active_since: int|None}.
    """
    if ts is None:
        ts = now_ts()
    stale_threshold = ts - ACTIVE_TAKEOVER_FACTOR * max(election_poll, 1)

    # Serialize the read-modify-write
    conn.execute("BEGIN IMMEDIATE")
    try:
        # Heartbeat the requester (must already be registered).
        cur = conn.execute(
            "UPDATE clients SET last_heartbeat=? WHERE client_id=? AND group_id=? AND role='C'",
            (ts, client_id, group_id),
        )
        if cur.rowcount == 0:
            conn.execute("COMMIT")
            return {"active": False, "active_client_id": None, "active_since": None, "error": "unknown_client"}

        # Find the current active in this group
        active_row = conn.execute(
            "SELECT client_id, last_heartbeat, active_since FROM clients "
            "WHERE group_id=? AND role='C' AND is_active=1 LIMIT 1",
            (group_id,),
        ).fetchone()

        winner_id = None
        winner_since = None

        if active_row is None:
            # No active — first comer wins. Tie-break by earliest registered_at.
            cand = conn.execute(
                "SELECT client_id, registered_at FROM clients "
                "WHERE group_id=? AND role='C' AND last_heartbeat>=? "
                "ORDER BY registered_at ASC, client_id ASC LIMIT 1",
                (group_id, stale_threshold),
            ).fetchone()
            if cand is None:
                # Even our own heartbeat puts us at ts; we should be in the candidate set.
                # Fallback: pick ourselves.
                winner_id = client_id
            else:
                winner_id = cand["client_id"]
            winner_since = ts
            conn.execute(
                "UPDATE clients SET is_active=1, active_since=? WHERE client_id=?",
                (winner_since, winner_id),
            )
        elif active_row["last_heartbeat"] < stale_threshold:
            # Active is stale — requester takes over (they're alive, otherwise they wouldn't be calling).
            winner_id = client_id
            winner_since = ts
            conn.execute("UPDATE clients SET is_active=0, active_since=NULL WHERE group_id=? AND role='C'", (group_id,))
            conn.execute(
                "UPDATE clients SET is_active=1, active_since=? WHERE client_id=?",
                (winner_since, winner_id),
            )
        else:
            winner_id = active_row["client_id"]
            winner_since = active_row["active_since"]

        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    return {
        "active": winner_id == client_id,
        "active_client_id": winner_id,
        "active_since": winner_since,
    }


def force_active(conn: sqlite3.Connection, group_id: str, client_id: str, *, ts: int | None = None) -> None:
    """Test helper: forcibly mark `client_id` as the active C in a group."""
    if ts is None:
        ts = now_ts()
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("UPDATE clients SET is_active=0, active_since=NULL WHERE group_id=? AND role='C'", (group_id,))
        conn.execute(
            "UPDATE clients SET is_active=1, active_since=?, last_heartbeat=? WHERE client_id=?",
            (ts, ts, client_id),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
