"""Test fixtures for llmrouter.

X/B is now a single unified process.  Tests talk to x_server via plain
HTTP (no TLS needed in tests); C connects via plain WS.

Session-scoped fixtures (x_server, mock_llm) are shared across the full
test run — roughly the same approach as before but simpler (no relay cert).
"""

import asyncio
import importlib
import os
import socket
import sys
import tempfile

import aiohttp
import pytest
import pytest_asyncio
from aiohttp import web

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TEST_TUNNEL_SECRET = "tun-test-secret-for-e2e"
TEST_GROUP_ID = "13800138000_test"
TEST_PHONE = "13800138000"
TEST_SUFFIX = "test"

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _setup_env(*, mock_llm_port: int, x_port: int, llmrouter_home: str,
               group_id: str = TEST_GROUP_ID):
    os.environ["INTERNAL_LLM_BASE"] = f"http://127.0.0.1:{mock_llm_port}"
    # X_BASE_URL tells C where to connect (plain HTTP in tests → ws:// WS)
    os.environ["X_BASE_URL"] = f"http://127.0.0.1:{x_port}"
    os.environ["GROUP_ID"] = group_id
    os.environ["LLMROUTER_HOME"] = llmrouter_home
    os.environ["X_HEARTBEAT_INTERVAL"] = "30"
    os.environ["X_AUDIT_BATCH_INTERVAL"] = "1"
    os.environ["ELECTION_POLL_INTERVAL"] = "1"
    # Per-role client id for C
    os.environ.setdefault("CLIENT_ID_C", "client-c-test")
    # C uses plain WS in tests (X_BASE_URL is http → ws://)
    os.environ["RELAY_TLS"] = "false"
    os.environ["TUNNEL_SECRET"] = TEST_TUNNEL_SECRET


def _reload_config():
    import config
    importlib.reload(config)
    return config


@pytest_asyncio.fixture(scope="session")
async def mock_llm():
    from mock_llm import handle_chat, handle_models

    received_headers = []

    @web.middleware
    async def capture_headers(request, handler):
        received_headers.append(dict(request.headers))
        return await handler(request)

    app = web.Application(middlewares=[capture_headers])
    app.router.add_post("/v1/chat/completions", handle_chat)
    app.router.add_post("/anthropic/v1/messages", handle_chat)
    app.router.add_get("/v1/models", handle_models)
    app["received_headers"] = received_headers

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    app["port"] = port

    yield app

    await runner.cleanup()


def _seed_test_group(app, group_id=TEST_GROUP_ID, tunnel_secret=TEST_TUNNEL_SECRET):
    from x.application.group_service import GroupService
    svc: GroupService = app["services"]["group"]
    if svc.get_group(group_id) is None:
        phone, suffix = group_id.split("_", 1)
        svc.create_group(phone, suffix, tunnel_secret=tunnel_secret)


def _seed_c_client_active(app, group_id, client_id):
    from x.application.registration_service import RegistrationService
    from x.infrastructure.repositories.sqlite_client_repo import SqliteClientRepository
    reg_svc: RegistrationService = app["services"]["registration"]
    client_repo: SqliteClientRepository = app["services"]["client_repo"]
    reg_svc.register_c(group_id, client_id, hostname="test", version="0.0.1")
    # 使用 SqliteClientRepository 的测试辅助方法强制设置 active
    import time
    client_repo.force_active_for_test(group_id, client_id, int(time.time()))


@pytest_asyncio.fixture(scope="session")
async def x_server(mock_llm):
    """Unified X+relay server running as plain HTTP.

    Replaces the old separate `relay` fixture.  Serves both control-plane
    (/api/*, /install/*) and data-plane (/ws/notifications, /g/{group_id}/*).
    """
    tmp_home = tempfile.mkdtemp(prefix="llmrouter-test-")
    db_path = os.path.join(tmp_home, "data", "x.sqlite")
    releases_dir = os.path.join(tmp_home, "releases")
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    os.makedirs(releases_dir, exist_ok=True)

    from x.server import create_app
    port = _free_port()
    app = create_app(
        db_path=db_path,
        releases_dir=releases_dir,
        x_base_url=f"http://127.0.0.1:{port}",
        heartbeat_interval=30,
        election_poll=1,
    )

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()

    # Pre-seed the test group with the fixed tunnel_secret so WS auth works.
    _seed_test_group(app)

    _setup_env(
        mock_llm_port=mock_llm["port"],
        x_port=port,
        llmrouter_home=tmp_home,
    )
    _reload_config()

    info = {
        "app": app,
        "port": port,
        "db_path": db_path,
        "releases_dir": releases_dir,
        "home": tmp_home,
        "url": f"http://127.0.0.1:{port}",
    }

    yield info

    await runner.cleanup()
    import shutil
    shutil.rmtree(tmp_home, ignore_errors=True)


@pytest_asyncio.fixture
async def tunnel(x_server, mock_llm):
    """Start TunnelWorker (C) connecting to X's WS endpoint."""
    _reload_config()

    # Ensure C client is marked active before the WS connection attempt.
    _seed_c_client_active(x_server["app"], TEST_GROUP_ID, os.environ["CLIENT_ID_C"])

    from c.settings import CSettings
    from c.tunnel_worker import TunnelWorker

    settings = CSettings(
        x_base_url=x_server["url"],
        group_id=TEST_GROUP_ID,
        client_id=os.environ["CLIENT_ID_C"],
        tunnel_secret=TEST_TUNNEL_SECRET,
        internal_llm_base=f"http://127.0.0.1:{mock_llm['port']}",
        cache_dir=os.path.join(x_server["home"], "cache"),
        election_poll_interval=1,
        heartbeat_interval=30,
    )
    worker = TunnelWorker(settings)
    task = asyncio.create_task(worker.start())

    relay = x_server["app"]["relay"]
    for _ in range(50):
        if TEST_GROUP_ID in relay.tunnels and not relay.tunnels[TEST_GROUP_ID].closed:
            break
        await asyncio.sleep(0.1)
    else:
        raise RuntimeError("Tunnel client did not connect within 5s")

    yield worker

    worker._running = False
    ws = relay.tunnels.get(TEST_GROUP_ID)
    if ws and not ws.closed:
        await ws.close()
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass
    if worker.session:
        await worker.session.close()


@pytest_asyncio.fixture
async def full_chain(x_server, mock_llm, tunnel):
    """Full chain ready: x_server (X+relay) + mock_llm + C tunnel all connected."""
    yield {
        "x_server": x_server,
        "mock_llm": mock_llm,
        "tunnel": tunnel,
        "x_port": x_server["port"],
        "mock_llm_port": mock_llm["port"],
    }


@pytest_asyncio.fixture
async def client(x_server):
    """Plain HTTP client for calling A→X relay endpoints (/g/{group_id}/...)."""
    session = aiohttp.ClientSession()
    yield session
    await session.close()


@pytest_asyncio.fixture
async def http_client():
    """Plain HTTP client for X control-plane endpoints."""
    session = aiohttp.ClientSession()
    yield session
    await session.close()
