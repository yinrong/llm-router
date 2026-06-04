"""Stability tests: verify C stays connected across repeated X restarts.

These tests simulate real-world scenarios where the X server restarts
frequently while C is running in the background.  C should automatically
reconnect and resume serving requests without intervention.
"""

import asyncio
import importlib
import os
import socket
import tempfile

import aiohttp
import pytest
import pytest_asyncio
from aiohttp import web

TEST_GROUP_ID = "13800138000_test"
TEST_TUNNEL_SECRET = "tun-test-secret-for-e2e"


# ── helpers ──────────────────────────────────────────────────────────────────

def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


async def _start_x(db_path: str, releases_dir: str, port: int, mock_llm_port: int):
    """Start a fresh X+relay app and seed the test group.

    We only seed the group (for WS auth); no election setup needed since
    stability tests use GROUP_ID='' (no XClient, always-active event).
    """
    from x.server import create_app
    from x import db as xdb

    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    os.makedirs(releases_dir, exist_ok=True)

    app = create_app(
        db_path=db_path,
        releases_dir=releases_dir,
        x_base_url=f"http://127.0.0.1:{port}",
        heartbeat_interval=30,
        election_poll=1,
    )
    conn = app["db"]

    if xdb.get_group(conn, TEST_GROUP_ID) is None:
        xdb.create_group(conn, "13800138000", "test", tunnel_secret=TEST_TUNNEL_SECRET)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    return app, runner


def _apply_env(port: int, mock_llm_port: int) -> dict:
    """Set env vars for stability tests. Returns old values to restore.

    GROUP_ID is intentionally left empty so Worker uses _always_active_event
    (no election round-trip needed) — making reconnect tests deterministic.
    The WS auth still works because we seed the group in _start_x.
    """
    keys = ["X_BASE_URL", "INTERNAL_LLM_BASE", "TUNNEL_SECRET", "GROUP_ID",
            "RELAY_TLS", "CLIENT_ID_C", "ELECTION_POLL_INTERVAL", "LLMROUTER_HOME"]
    old = {k: os.environ.get(k) for k in keys}
    os.environ["X_BASE_URL"] = f"http://127.0.0.1:{port}"
    os.environ["INTERNAL_LLM_BASE"] = f"http://127.0.0.1:{mock_llm_port}"
    os.environ["TUNNEL_SECRET"] = TEST_TUNNEL_SECRET
    os.environ["GROUP_ID"] = ""  # no election — Worker uses always-active event
    os.environ["RELAY_TLS"] = "false"
    os.environ.setdefault("CLIENT_ID_C", "client-c-test")
    os.environ["ELECTION_POLL_INTERVAL"] = "1"
    return old


def _restore_env(old: dict) -> None:
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def _reload_c_modules():
    """Reload config + C modules so they pick up new env vars."""
    import config
    importlib.reload(config)
    # Speed up reconnect for stability tests
    config.RECONNECT_BASE = 1
    config.RECONNECT_MAX = 2
    import c_x_client
    importlib.reload(c_x_client)
    import _server
    importlib.reload(_server)


async def _start_worker():
    """Instantiate a Worker. With GROUP_ID='', no XClient is created and
    Worker uses _always_active_event (set by default) so it connects immediately
    without waiting for an election round-trip."""
    import _server
    worker = _server.Worker()
    # Always-active path: Worker._active_event is already set.
    return worker


async def _wait_c_connected(relay, timeout: float = 8.0) -> bool:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if TEST_GROUP_ID in relay.tunnels and not relay.tunnels[TEST_GROUP_ID].closed:
            return True
        await asyncio.sleep(0.1)
    return False


async def _make_request(session: aiohttp.ClientSession, port: int, tag: str) -> int:
    try:
        async with session.post(
            f"http://127.0.0.1:{port}/g/{TEST_GROUP_ID}/v1/chat/completions",
            json={"model": "test", "messages": [{"role": "user", "content": tag}]},
            headers={"content-type": "application/json"},
            timeout=aiohttp.ClientTimeout(total=5),
        ) as resp:
            return resp.status
    except aiohttp.ClientConnectorError:
        return 503


async def _stop_worker(worker, relay=None):
    """Cleanly stop a Worker and its session."""
    worker._running = False
    if relay is not None:
        ws = relay.tunnels.get(TEST_GROUP_ID)
        if ws and not ws.closed:
            await ws.close()
    if worker.session:
        await worker.session.close()


# ── fixture ───────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def stab_env(mock_llm):
    """Per-test tmp directory + isolated port."""
    tmp = tempfile.mkdtemp(prefix="stab-")
    db_path = os.path.join(tmp, "data", "x.sqlite")
    releases_dir = os.path.join(tmp, "releases")
    port = _free_port()
    mock_llm_port = mock_llm["port"]

    old_env = _apply_env(port, mock_llm_port)
    _reload_c_modules()

    yield {
        "db_path": db_path, "releases_dir": releases_dir,
        "port": port, "mock_llm_port": mock_llm_port, "tmp": tmp,
    }

    _restore_env(old_env)
    _reload_c_modules()  # restore modules to session-scoped state

    import shutil
    shutil.rmtree(tmp, ignore_errors=True)


# ── tests ─────────────────────────────────────────────────────────────────────

async def test_c_reconnects_after_single_x_restart(stab_env):
    """C auto-reconnects after X restarts once. Requests succeed after reconnect."""
    d = stab_env
    app, runner = await _start_x(d["db_path"], d["releases_dir"], d["port"], d["mock_llm_port"])
    relay = app["relay"]

    worker = await _start_worker()
    c_task = asyncio.create_task(worker.start())
    assert await _wait_c_connected(relay), "C did not connect to X initially"

    session = aiohttp.ClientSession()
    assert await _make_request(session, d["port"], "before-restart") == 200

    # Close active tunnel first so AppRunner.cleanup() doesn't hang waiting for WS
    old_ws = relay.tunnels.get(TEST_GROUP_ID)
    if old_ws and not old_ws.closed:
        await old_ws.close()
    await asyncio.sleep(0.1)
    await runner.cleanup()
    await asyncio.sleep(0.2)

    app2, runner2 = await _start_x(d["db_path"], d["releases_dir"], d["port"], d["mock_llm_port"])
    relay2 = app2["relay"]

    assert await _wait_c_connected(relay2, timeout=10.0), "C did not reconnect after X restart"
    assert await _make_request(session, d["port"], "after-restart") == 200

    await session.close()
    await _stop_worker(worker, relay2)
    c_task.cancel()
    try:
        await c_task
    except (asyncio.CancelledError, Exception):
        pass
    await runner2.cleanup()


async def test_c_survives_repeated_x_restarts(stab_env):
    """C reconnects reliably across 5 rapid X restarts."""
    d = stab_env
    cur_app, cur_runner = await _start_x(d["db_path"], d["releases_dir"], d["port"], d["mock_llm_port"])

    worker = await _start_worker()
    c_task = asyncio.create_task(worker.start())
    assert await _wait_c_connected(cur_app["relay"]), "Initial connection failed"

    session = aiohttp.ClientSession()

    for i in range(3):
        # Close the active tunnel before stopping X so AppRunner.cleanup() doesn't hang
        old_ws = cur_app["relay"].tunnels.get(TEST_GROUP_ID)
        if old_ws and not old_ws.closed:
            await old_ws.close()
        await asyncio.sleep(0.1)
        await cur_runner.cleanup()
        await asyncio.sleep(0.1)

        cur_app, cur_runner = await _start_x(d["db_path"], d["releases_dir"], d["port"], d["mock_llm_port"])

        connected = await _wait_c_connected(cur_app["relay"], timeout=8.0)
        assert connected, f"C did not reconnect after restart {i + 1}/3"

        status = await _make_request(session, d["port"], f"restart-{i}")
        assert status == 200, f"Request failed after restart {i + 1}: status {status}"

    await session.close()
    await _stop_worker(worker, cur_app["relay"])
    c_task.cancel()
    try:
        await c_task
    except (asyncio.CancelledError, Exception):
        pass
    await cur_runner.cleanup()


async def test_requests_during_x_downtime_return_error(stab_env):
    """Requests while X is down return an error (not hang), then work after restart."""
    d = stab_env
    app, runner = await _start_x(d["db_path"], d["releases_dir"], d["port"], d["mock_llm_port"])
    relay = app["relay"]

    worker = await _start_worker()
    c_task = asyncio.create_task(worker.start())
    assert await _wait_c_connected(relay), "Initial connection failed"

    session = aiohttp.ClientSession()
    assert await _make_request(session, d["port"], "pre-shutdown") == 200

    # Close active tunnel first so AppRunner.cleanup() doesn't hang
    old_ws = relay.tunnels.get(TEST_GROUP_ID)
    if old_ws and not old_ws.closed:
        await old_ws.close()
    await asyncio.sleep(0.1)
    await runner.cleanup()
    await asyncio.sleep(0.2)

    # Request while X is down should fail fast, NOT hang
    status = await _make_request(session, d["port"], "during-shutdown")
    assert status in (502, 503, 504), f"Expected 5xx, got {status}"

    # Restart X, C should reconnect automatically (always-active event stays set)
    app2, runner2 = await _start_x(d["db_path"], d["releases_dir"], d["port"], d["mock_llm_port"])
    assert await _wait_c_connected(app2["relay"], timeout=10.0), "C did not reconnect"
    assert await _make_request(session, d["port"], "after-restart") == 200

    await session.close()
    await _stop_worker(worker, app2["relay"])
    c_task.cancel()
    try:
        await c_task
    except (asyncio.CancelledError, Exception):
        pass
    await runner2.cleanup()


async def test_multiple_c_only_one_tunnel_active(stab_env):
    """When a Worker connects, exactly one WS tunnel is active at a time.
    A second WS connection from the same group would displace the first (relay semantics).
    """
    d = stab_env
    app, runner = await _start_x(d["db_path"], d["releases_dir"], d["port"], d["mock_llm_port"])
    relay = app["relay"]

    worker = await _start_worker()
    c_task = asyncio.create_task(worker.start())
    assert await _wait_c_connected(relay), "C did not connect"

    # Exactly one tunnel open for this group
    open_tunnels = [g for g, ws in relay.tunnels.items() if g == TEST_GROUP_ID and not ws.closed]
    assert len(open_tunnels) == 1, f"Expected 1 tunnel, got {len(open_tunnels)}"

    session = aiohttp.ClientSession()
    assert await _make_request(session, d["port"], "one-tunnel") == 200

    await session.close()
    await _stop_worker(worker, relay)
    c_task.cancel()
    try:
        await c_task
    except (asyncio.CancelledError, Exception):
        pass
    await runner.cleanup()
