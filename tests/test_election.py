"""Leader election tests."""

import sqlite3
import time

import pytest

from x import db as xdb
from x import election as xelection


async def _create_group(http_client, url, phone, suffix):
    async with http_client.post(f"{url}/api/groups", json={"phone": phone, "suffix": suffix}) as resp:
        assert resp.status == 201, await resp.text()
        return await resp.json()


async def _register_c(http_client, url, group_id, client_id):
    async with http_client.post(
        f"{url}/api/register/c",
        json={"group_id": group_id, "client_id": client_id, "version": "0.0.1", "hostname": "test"},
    ) as resp:
        assert resp.status == 200, await resp.text()
        return await resp.json()


async def _elect(http_client, url, group_id, client_id):
    async with http_client.post(
        f"{url}/api/elect/{group_id}",
        json={"client_id": client_id},
    ) as resp:
        return resp.status, await resp.json()


async def test_single_c_becomes_active(x_server, http_client):
    gid = "13902000001_e1"
    await _create_group(http_client, x_server["url"], "13902000001", "e1")
    await _register_c(http_client, x_server["url"], gid, "c-solo")
    status, body = await _elect(http_client, x_server["url"], gid, "c-solo")
    assert status == 200, body
    assert body["active"] is True
    assert body["active_client_id"] == "c-solo"


async def test_two_cs_only_one_active(x_server, http_client):
    gid = "13902000002_e2"
    await _create_group(http_client, x_server["url"], "13902000002", "e2")
    await _register_c(http_client, x_server["url"], gid, "c-a")
    await _register_c(http_client, x_server["url"], gid, "c-b")

    status, body = await _elect(http_client, x_server["url"], gid, "c-a")
    assert status == 200, body
    assert body["active"] is True
    assert body["active_client_id"] == "c-a"

    status, body = await _elect(http_client, x_server["url"], gid, "c-b")
    assert status == 200, body
    assert body["active"] is False
    assert body["active_client_id"] == "c-a"

    status, body = await _elect(http_client, x_server["url"], gid, "c-a")
    assert status == 200, body
    assert body["active"] is True
    assert body["active_client_id"] == "c-a"


async def test_unknown_client_in_election(x_server, http_client):
    gid = "13902000003_e3"
    await _create_group(http_client, x_server["url"], "13902000003", "e3")
    # Do NOT register any C client.
    status, body = await _elect(http_client, x_server["url"], gid, "c-ghost")
    assert status == 404, body


async def test_active_failover_via_db(x_server, http_client):
    gid = "13902000004_e4"
    await _create_group(http_client, x_server["url"], "13902000004", "e4")

    conn = x_server["app"]["db"]
    xdb.upsert_client(conn, client_id="c-a-fo", group_id=gid, role="C", hostname="test", version="0.0.1")
    xdb.upsert_client(conn, client_id="c-b-fo", group_id=gid, role="C", hostname="test", version="0.0.1")

    res = xelection.claim_active(conn, gid, "c-a-fo", election_poll=1, ts=1000)
    assert res["active"] is True
    assert res["active_client_id"] == "c-a-fo"

    res = xelection.claim_active(conn, gid, "c-b-fo", election_poll=1, ts=1001)
    assert res["active"] is False
    assert res["active_client_id"] == "c-a-fo"

    # ts=1010, c-a's last_heartbeat is still 1000 (we didn't refresh it),
    # threshold = 1010 - 2*1 = 1008, 1000 < 1008 => c-a stale, c-b takes over.
    res = xelection.claim_active(conn, gid, "c-b-fo", election_poll=1, ts=1010)
    assert res["active"] is True
    assert res["active_client_id"] == "c-b-fo"


async def test_election_acts_as_heartbeat(x_server, http_client):
    gid = "13902000005_e5"
    await _create_group(http_client, x_server["url"], "13902000005", "e5")
    await _register_c(http_client, x_server["url"], gid, "c-hb")

    before = int(time.time())
    status, body = await _elect(http_client, x_server["url"], gid, "c-hb")
    assert status == 200, body
    assert body["active"] is True
    after = int(time.time())

    conn = x_server["app"]["db"]
    row = conn.execute(
        "SELECT last_heartbeat FROM clients WHERE client_id=?",
        ("c-hb",),
    ).fetchone()
    assert row is not None
    last_hb = row["last_heartbeat"]
    # Allow small clock-skew slack on either side.
    assert before - 1 <= last_hb <= after + 1
