"""Tests for the X coordinator."""

import json
import sqlite3

import pytest

# pytest-asyncio auto mode picks up async tests; no need for @pytest.mark.asyncio


async def test_create_group_valid(x_server, http_client):
    async with http_client.post(
        f"{x_server['url']}/api/groups",
        json={"phone": "13900000001", "suffix": "alpha"},
    ) as resp:
        assert resp.status == 201
        body = await resp.json()
        assert body["group_id"] == "13900000001_alpha"
        assert body["tunnel_secret"]
        assert body["tunnel_secret"].startswith("tun-")


async def test_create_group_invalid_phone_short(x_server, http_client):
    async with http_client.post(
        f"{x_server['url']}/api/groups",
        json={"phone": "12345", "suffix": "alpha"},
    ) as resp:
        assert resp.status == 400


async def test_create_group_invalid_phone_letters(x_server, http_client):
    async with http_client.post(
        f"{x_server['url']}/api/groups",
        json={"phone": "abcdefghijk", "suffix": "alpha"},
    ) as resp:
        assert resp.status == 400


async def test_create_group_invalid_suffix_long(x_server, http_client):
    async with http_client.post(
        f"{x_server['url']}/api/groups",
        json={"phone": "13900000010", "suffix": "a" * 33},
    ) as resp:
        assert resp.status == 400


async def test_create_group_invalid_suffix_special(x_server, http_client):
    async with http_client.post(
        f"{x_server['url']}/api/groups",
        json={"phone": "13900000011", "suffix": "bad!suffix"},
    ) as resp:
        assert resp.status == 400


async def test_create_group_duplicate(x_server, http_client):
    payload = {"phone": "13900000020", "suffix": "dup"}
    async with http_client.post(f"{x_server['url']}/api/groups", json=payload) as resp:
        assert resp.status == 201
    async with http_client.post(f"{x_server['url']}/api/groups", json=payload) as resp:
        assert resp.status == 409


async def test_list_groups_filter_by_phone(x_server, http_client):
    for phone, suffix in [
        ("13900000002", "x"),
        ("13900000002", "y"),
        ("13900000003", "z"),
    ]:
        async with http_client.post(
            f"{x_server['url']}/api/groups",
            json={"phone": phone, "suffix": suffix},
        ) as resp:
            assert resp.status == 201

    async with http_client.get(
        f"{x_server['url']}/api/groups", params={"phone": "13900000002"}
    ) as resp:
        assert resp.status == 200
        body = await resp.json()
        groups = body["groups"]
        assert len(groups) == 2
        for g in groups:
            assert g["phone"] == "13900000002"


async def test_get_group_404(x_server, http_client):
    async with http_client.get(f"{x_server['url']}/api/groups/00000000000_nope") as resp:
        assert resp.status == 404


async def test_get_group_with_clients(x_server, http_client):
    phone, suffix = "13900000030", "withclients"
    group_id = f"{phone}_{suffix}"

    async with http_client.post(
        f"{x_server['url']}/api/groups",
        json={"phone": phone, "suffix": suffix},
    ) as resp:
        assert resp.status == 201

    async with http_client.post(
        f"{x_server['url']}/api/register/b",
        json={
            "group_id": group_id,
            "client_id": f"b-{group_id}",
            "public_addr": "127.0.0.1",
            "port": 9443,
            "version": "0.0.1",
            "hostname": "test-b",
        },
    ) as resp:
        assert resp.status == 200

    async with http_client.post(
        f"{x_server['url']}/api/register/c",
        json={
            "group_id": group_id,
            "client_id": f"c-{group_id}",
            "version": "0.0.1",
            "hostname": "test-c",
        },
    ) as resp:
        assert resp.status == 200

    async with http_client.get(f"{x_server['url']}/api/groups/{group_id}") as resp:
        assert resp.status == 200
        body = await resp.json()
        clients = body.get("clients") or []
        assert len(clients) >= 2
        roles = sorted(c["role"] for c in clients)
        assert "B" in roles
        assert "C" in roles


async def test_version_endpoint(x_server, http_client):
    async with http_client.get(f"{x_server['url']}/api/version/c") as resp:
        assert resp.status == 200
        body = await resp.json()
        assert "version" in body
        assert "available" in body


async def test_version_endpoint_unknown_role(x_server, http_client):
    async with http_client.get(f"{x_server['url']}/api/version/z") as resp:
        assert resp.status == 404


async def test_install_script_renders_b(x_server, http_client):
    # B is now co-located with X; /install/b.sh returns a deprecation notice.
    async with http_client.get(f"{x_server['url']}/install/b.sh") as resp:
        assert resp.status == 200
        body = await resp.text()
        assert "install/c.sh" in body  # points users to C installer


async def test_install_script_renders_c(x_server, http_client):
    group_id = "13900000004_install"
    async with http_client.get(
        f"{x_server['url']}/install/c.sh", params={"group_id": group_id}
    ) as resp:
        assert resp.status == 200
        body = await resp.text()
        assert "INTERNAL_LLM_BASE" in body
        assert "set -euo pipefail" in body
        assert group_id in body


async def test_install_script_a_ps1(x_server, http_client):
    async with http_client.get(f"{x_server['url']}/install/a.ps1") as resp:
        assert resp.status == 200
        body = await resp.text()
        assert body
        assert "wsl" in body.lower()


async def test_audit_post_and_query(x_server, http_client):
    phone, suffix = "13900000005", "aud"
    group_id = f"{phone}_{suffix}"
    async with http_client.post(
        f"{x_server['url']}/api/groups",
        json={"phone": phone, "suffix": suffix},
    ) as resp:
        assert resp.status == 201

    async with http_client.post(
        f"{x_server['url']}/api/audit",
        json={
            "group_id": group_id,
            "b_client_id": "b1",
            "events": [
                {"ts": 1234, "method": "POST", "path": "/v1/x", "status": 200, "latency_ms": 5}
            ],
        },
    ) as resp:
        assert resp.status == 200
        body = await resp.json()
        assert body == {"accepted": 1}

    conn = sqlite3.connect(x_server["db_path"])
    try:
        rows = conn.execute(
            "SELECT method, status FROM audit_log WHERE group_id=?",
            (group_id,),
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 1
    assert rows[0][0] == "POST"
    assert rows[0][1] == 200


async def test_audit_unknown_group_404(x_server, http_client):
    async with http_client.post(
        f"{x_server['url']}/api/audit",
        json={
            "group_id": "13900000099_ghost",
            "b_client_id": "b1",
            "events": [{"ts": 1, "method": "GET", "path": "/", "status": 200, "latency_ms": 1}],
        },
    ) as resp:
        assert resp.status == 404


async def test_healthz(x_server, http_client):
    async with http_client.get(f"{x_server['url']}/healthz") as resp:
        assert resp.status == 200
        body = await resp.json()
        assert body.get("ok") is True
        assert "version" in body


async def test_download_release_missing(x_server, http_client):
    async with http_client.get(f"{x_server['url']}/api/download/b/0.0.1") as resp:
        assert resp.status == 404
