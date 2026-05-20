"""End-to-end audit log propagation tests."""

import asyncio
import sqlite3
import time

TEST_GROUP_ID = "13800138000_test"


def _read_audit_rows(db_path, group_id):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT method, path, status, latency_ms FROM audit_log WHERE group_id=? ORDER BY id",
            (group_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


async def _wait_for_audit(db_path, group_id, *, predicate, timeout=2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        rows = _read_audit_rows(db_path, group_id)
        if any(predicate(r) for r in rows):
            return rows
        await asyncio.sleep(0.2)
    return _read_audit_rows(db_path, group_id)


async def test_audit_e2e_normal_request(full_chain, client):
    x_port = full_chain["x_port"]
    db_path = full_chain["x_server"]["db_path"]

    async with client.post(
        f"http://127.0.0.1:{x_port}/g/{TEST_GROUP_ID}/v1/chat/completions",
        json={"model": "test", "messages": [{"role": "user", "content": "audit-test"}]},
        headers={"content-type": "application/json"},
    ) as resp:
        assert resp.status == 200
        await resp.json()

    rows = await _wait_for_audit(
        db_path,
        TEST_GROUP_ID,
        predicate=lambda r: r["status"] == 200 and r["path"] == "/v1/chat/completions",
    )
    matching = [r for r in rows if r["status"] == 200 and r["path"] == "/v1/chat/completions"]
    assert matching, f"no matching audit row in: {rows}"
    assert matching[0]["method"] == "POST"
    assert matching[0]["latency_ms"] is not None
    assert matching[0]["latency_ms"] > 0


async def test_audit_e2e_no_tunnel_502(x_server, client):
    """Without a tunnel attached, B should respond 502 and still emit an audit row."""
    x_port = x_server["port"]
    db_path = x_server["db_path"]

    relay = x_server["app"]["relay"]
    ws = relay.tunnels.get(TEST_GROUP_ID)
    if ws is not None and not ws.closed:
        await ws.close()
    # Give the close handler a chance to clear the reference.
    for _ in range(50):
        ws = relay.tunnels.get(TEST_GROUP_ID)
        if ws is None or ws.closed:
            break
        await asyncio.sleep(0.1)

    async with client.post(
        f"http://127.0.0.1:{x_port}/g/{TEST_GROUP_ID}/v1/chat/completions",
        json={"model": "test", "messages": [{"role": "user", "content": "no-tunnel"}]},
        headers={"content-type": "application/json"},
    ) as resp:
        assert resp.status == 502

    rows = await _wait_for_audit(
        db_path,
        TEST_GROUP_ID,
        predicate=lambda r: r["status"] == 502,
    )
    matching = [r for r in rows if r["status"] == 502]
    assert matching, f"no 502 audit row in: {rows}"


async def test_audit_direct_post(x_server, http_client):
    """Pure X-side test: create a unique group, POST two audit events, verify ordering in sqlite."""
    phone, suffix = "13903000001", "direct"
    group_id = f"{phone}_{suffix}"

    async with http_client.post(
        f"{x_server['url']}/api/groups",
        json={"phone": phone, "suffix": suffix},
    ) as resp:
        assert resp.status == 201

    events = [
        {"ts": 1000, "method": "GET", "path": "/v1/models", "status": 200, "latency_ms": 3},
        {"ts": 2000, "method": "POST", "path": "/v1/chat/completions", "status": 502, "latency_ms": 7},
    ]
    async with http_client.post(
        f"{x_server['url']}/api/audit",
        json={"group_id": group_id, "b_client_id": "b-direct", "events": events},
    ) as resp:
        assert resp.status == 200
        body = await resp.json()
        assert body == {"accepted": 2}

    rows = _read_audit_rows(x_server["db_path"], group_id)
    assert len(rows) == 2
    assert rows[0]["method"] == "GET"
    assert rows[0]["path"] == "/v1/models"
    assert rows[0]["status"] == 200
    assert rows[0]["latency_ms"] == 3
    assert rows[1]["method"] == "POST"
    assert rows[1]["path"] == "/v1/chat/completions"
    assert rows[1]["status"] == 502
    assert rows[1]["latency_ms"] == 7
