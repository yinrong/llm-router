"""Leader election tests."""

import time

import pytest


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

    reg_svc = x_server["app"]["services"]["registration"]
    elect_svc = x_server["app"]["services"]["election"]

    reg_svc.register_c(gid, "c-a-fo", hostname="test", version="0.0.1", ts=990)
    reg_svc.register_c(gid, "c-b-fo", hostname="test", version="0.0.1", ts=991)

    res = elect_svc.claim_active(gid, "c-a-fo", election_poll=1, ts=1000)
    assert res["active"] is True
    assert res["active_client_id"] == "c-a-fo"

    res = elect_svc.claim_active(gid, "c-b-fo", election_poll=1, ts=1001)
    assert res["active"] is False
    assert res["active_client_id"] == "c-a-fo"

    # ts=1010, c-a's last_heartbeat is still 1000 (we didn't refresh it),
    # threshold = 1010 - 2*1 = 1008, 1000 < 1008 => c-a stale, c-b takes over.
    res = elect_svc.claim_active(gid, "c-b-fo", election_poll=1, ts=1010)
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

    client_repo = x_server["app"]["services"]["client_repo"]
    c = client_repo.get("c-hb")
    assert c is not None
    last_hb = c.last_heartbeat
    # Allow small clock-skew slack on either side.
    assert before - 1 <= last_hb <= after + 1
