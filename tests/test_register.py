"""B/C registration & heartbeat tests."""

import asyncio
import sqlite3


async def _create_group(http_client, url, phone, suffix):
    async with http_client.post(f"{url}/api/groups", json={"phone": phone, "suffix": suffix}) as resp:
        assert resp.status == 201, await resp.text()
        return await resp.json()


async def test_b_register_returns_secret(x_server, http_client):
    g = await _create_group(http_client, x_server["url"], "13901000001", "b1")
    async with http_client.post(
        f"{x_server['url']}/api/register/b",
        json={
            "group_id": "13901000001_b1",
            "client_id": "b-test-1",
            "public_addr": "1.2.3.4",
            "port": 8443,
            "version": "0.0.1",
            "hostname": "host",
        },
    ) as resp:
        assert resp.status == 200, await resp.text()
        body = await resp.json()
        assert body["tunnel_secret"] == g["tunnel_secret"]
        assert isinstance(body["heartbeat_interval"], int)


async def test_b_register_unknown_group_404(x_server, http_client):
    async with http_client.post(
        f"{x_server['url']}/api/register/b",
        json={
            "group_id": "13901000002_ghost",
            "client_id": "b-test-ghost",
            "public_addr": "1.2.3.4",
            "port": 8443,
            "version": "0.0.1",
            "hostname": "host",
        },
    ) as resp:
        assert resp.status == 404


async def test_b_register_missing_fields_400(x_server, http_client):
    async with http_client.post(
        f"{x_server['url']}/api/register/b",
        json={
            "client_id": "b-test-no-group",
            "public_addr": "1.2.3.4",
            "port": 8443,
        },
    ) as resp:
        assert resp.status == 400


async def test_b_register_updates_b_addr(x_server, http_client):
    await _create_group(http_client, x_server["url"], "13901000003", "baddr")
    group_id = "13901000003_baddr"
    async with http_client.post(
        f"{x_server['url']}/api/register/b",
        json={
            "group_id": group_id,
            "client_id": "b-test-baddr",
            "public_addr": "5.6.7.8",
            "port": 9443,
            "version": "0.0.1",
            "hostname": "host",
        },
    ) as resp:
        assert resp.status == 200, await resp.text()

    async with http_client.get(f"{x_server['url']}/api/groups/{group_id}") as resp:
        assert resp.status == 200
        body = await resp.json()
        assert body["b_addr"] == "5.6.7.8"
        assert body["b_port"] == 9443


async def test_c_register_returns_relay_addr(x_server, http_client):
    await _create_group(http_client, x_server["url"], "13901000004", "creg")
    group_id = "13901000004_creg"

    async with http_client.post(
        f"{x_server['url']}/api/register/b",
        json={
            "group_id": group_id,
            "client_id": "b-test-creg",
            "public_addr": "10.0.0.1",
            "port": 8443,
            "version": "0.0.1",
            "hostname": "host-b",
        },
    ) as resp:
        assert resp.status == 200, await resp.text()

    async with http_client.post(
        f"{x_server['url']}/api/register/c",
        json={
            "group_id": group_id,
            "client_id": "c-test-creg",
            "version": "0.0.1",
            "hostname": "host-c",
        },
    ) as resp:
        assert resp.status == 200, await resp.text()
        body = await resp.json()
        assert body["relay_addr"] == "10.0.0.1"
        assert body["relay_port"] == 8443
        assert body["tunnel_secret"]
        assert isinstance(body["heartbeat_interval"], int)
        assert isinstance(body["election_poll"], int)


async def test_c_register_with_no_b(x_server, http_client):
    await _create_group(http_client, x_server["url"], "13901000005", "cnob")
    group_id = "13901000005_cnob"

    async with http_client.post(
        f"{x_server['url']}/api/register/c",
        json={
            "group_id": group_id,
            "client_id": "c-test-nob",
            "version": "0.0.1",
            "hostname": "host-c",
        },
    ) as resp:
        assert resp.status == 200, await resp.text()
        body = await resp.json()
        assert body["relay_addr"] == ""
        assert body["relay_port"] == 0


async def test_heartbeat_updates_last_seen(x_server, http_client):
    await _create_group(http_client, x_server["url"], "13901000006", "hb")
    group_id = "13901000006_hb"
    client_id = "b-hb-1"

    async with http_client.post(
        f"{x_server['url']}/api/register/b",
        json={
            "group_id": group_id,
            "client_id": client_id,
            "public_addr": "1.2.3.4",
            "port": 8443,
            "version": "0.0.1",
            "hostname": "host",
        },
    ) as resp:
        assert resp.status == 200, await resp.text()

    await asyncio.sleep(1.1)

    async with http_client.post(
        f"{x_server['url']}/api/heartbeat",
        json={"client_id": client_id, "role": "B", "group_id": group_id},
    ) as resp:
        assert resp.status == 200
        body = await resp.json()
        assert body == {"ok": True}

    conn = sqlite3.connect(x_server["db_path"])
    try:
        row = conn.execute(
            "SELECT registered_at, last_heartbeat FROM clients WHERE client_id=?",
            (client_id,),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    registered_at, last_heartbeat = row
    assert last_heartbeat > registered_at


async def test_heartbeat_unknown_client_404(x_server, http_client):
    async with http_client.post(
        f"{x_server['url']}/api/heartbeat",
        json={"client_id": "nonexistent-client-xyz", "role": "B"},
    ) as resp:
        assert resp.status == 404


async def test_register_idempotent(x_server, http_client):
    await _create_group(http_client, x_server["url"], "13901000007", "idem")
    group_id = "13901000007_idem"
    client_id = "b-idem-1"

    async with http_client.post(
        f"{x_server['url']}/api/register/b",
        json={
            "group_id": group_id,
            "client_id": client_id,
            "public_addr": "1.2.3.4",
            "port": 8443,
            "version": "0.0.1",
            "hostname": "host",
        },
    ) as resp:
        assert resp.status == 200, await resp.text()

    async with http_client.post(
        f"{x_server['url']}/api/register/b",
        json={
            "group_id": group_id,
            "client_id": client_id,
            "public_addr": "1.2.3.4",
            "port": 8443,
            "version": "0.0.2",
            "hostname": "host",
        },
    ) as resp:
        assert resp.status == 200, await resp.text()

    async with http_client.get(f"{x_server['url']}/api/groups/{group_id}") as resp:
        assert resp.status == 200
        body = await resp.json()
        clients = body.get("clients") or []
        matching = [c for c in clients if c["client_id"] == client_id]
        assert len(matching) == 1
        assert matching[0]["version"] == "0.0.2"
