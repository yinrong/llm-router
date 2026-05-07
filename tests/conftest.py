import asyncio
import importlib
import os
import socket
import ssl
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


def _ensure_certs():
    cert_path = os.path.join(PROJECT_ROOT, "certs", "server.crt")
    if not os.path.exists(cert_path):
        sys.path.insert(0, PROJECT_ROOT)
        from gen_cert import generate_cert
        generate_cert(cert_dir=os.path.join(PROJECT_ROOT, "certs"))


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _setup_env(*, mock_llm_port, relay_port, x_port, llmrouter_home, group_id=TEST_GROUP_ID, client_id_b="client-b-test", client_id_c="client-c-test"):
    os.environ["RELAY_HOST"] = "127.0.0.1"
    os.environ["RELAY_PORT"] = str(relay_port)
    os.environ["RELAY_ADDR"] = "127.0.0.1"
    os.environ["TUNNEL_SECRET"] = TEST_TUNNEL_SECRET
    os.environ["INTERNAL_LLM_BASE"] = f"http://127.0.0.1:{mock_llm_port}"
    os.environ["CERT_FILE"] = os.path.join(PROJECT_ROOT, "certs", "server.crt")
    os.environ["KEY_FILE"] = os.path.join(PROJECT_ROOT, "certs", "server.key")
    os.environ["RELAY_TLS"] = "true"

    os.environ["LLMROUTER_HOME"] = llmrouter_home
    os.environ["X_BASE_URL"] = f"http://127.0.0.1:{x_port}"
    os.environ["GROUP_ID"] = group_id
    os.environ["X_HEARTBEAT_INTERVAL"] = "30"
    os.environ["X_AUDIT_BATCH_INTERVAL"] = "1"
    os.environ["ELECTION_POLL_INTERVAL"] = "1"
    # Per-role client ids — set by the consumer fixture before its reload.
    os.environ.setdefault("CLIENT_ID_B", client_id_b)
    os.environ.setdefault("CLIENT_ID_C", client_id_c)


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


@pytest_asyncio.fixture(scope="session")
async def x_server():
    """Real X coordinator running over plain HTTP on 127.0.0.1:<random>.

    Uses real sqlite at a tmp path. No mocking. Reuses the same db file
    across tests in the session (mirrors the existing relay/mock_llm fixtures).
    """
    tmp_home = tempfile.mkdtemp(prefix="llmrouter-test-")
    db_path = os.path.join(tmp_home, "data", "x.sqlite")
    releases_dir = os.path.join(tmp_home, "releases")
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    os.makedirs(releases_dir, exist_ok=True)

    # Stash for tests/fixtures that need to talk to X directly.
    os.environ["LLMROUTER_HOME"] = tmp_home

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
    # Best-effort cleanup of tmp_home; ignore errors.
    import shutil
    try:
        shutil.rmtree(tmp_home, ignore_errors=True)
    except Exception:
        pass


def _seed_test_group(x_server, group_id=TEST_GROUP_ID, tunnel_secret=TEST_TUNNEL_SECRET):
    """Insert the test group with a fixed tunnel_secret so cookie-based WS auth
    on B can use the same secret across test runs without round-tripping X."""
    from x import db as xdb
    conn = x_server["app"]["db"]
    if xdb.get_group(conn, group_id) is None:
        phone, suffix = group_id.split("_", 1)
        xdb.create_group(conn, phone, suffix, tunnel_secret=tunnel_secret)


def _seed_b_client(x_server, group_id, client_id, addr, port):
    from x import db as xdb
    conn = x_server["app"]["db"]
    xdb.upsert_client(conn, client_id=client_id, group_id=group_id, role="B", hostname="test", version="0.0.1")
    xdb.update_b_addr(conn, group_id, addr, port)


def _seed_c_client_active(x_server, group_id, client_id):
    from x import db as xdb
    from x import election as xelection
    conn = x_server["app"]["db"]
    xdb.upsert_client(conn, client_id=client_id, group_id=group_id, role="C", hostname="test", version="0.0.1")
    xelection.force_active(conn, group_id, client_id)


@pytest_asyncio.fixture(scope="session")
async def relay(mock_llm, x_server):
    _ensure_certs()
    mock_llm_port = mock_llm["port"]

    relay_port = _free_port()
    _setup_env(
        mock_llm_port=mock_llm_port,
        relay_port=relay_port,
        x_port=x_server["port"],
        llmrouter_home=x_server["home"],
    )
    _reload_config()

    _seed_test_group(x_server)

    import b_x_client
    importlib.reload(b_x_client)
    import relay_server
    importlib.reload(relay_server)

    app = relay_server.create_app()  # builds XClient automatically via build_x_client_for_b

    ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    cert_file = os.path.join(PROJECT_ROOT, "certs", "server.crt")
    key_file = os.path.join(PROJECT_ROOT, "certs", "server.key")
    ssl_ctx.load_cert_chain(cert_file, key_file)

    runner = web.AppRunner(app)
    await runner.setup()  # triggers on_startup → x_client.start() registers with X
    site = web.TCPSite(runner, "127.0.0.1", relay_port, ssl_context=ssl_ctx)
    await site.start()

    app["port"] = relay_port
    app["x_server"] = x_server

    yield app

    await runner.cleanup()


@pytest_asyncio.fixture
async def tunnel(relay, x_server):
    """Start tunnel client (C) connecting to relay (B)."""
    _reload_config()

    _seed_c_client_active(x_server, TEST_GROUP_ID, os.environ["CLIENT_ID_C"])

    import _server
    importlib.reload(_server)

    worker = _server.Worker()
    task = asyncio.create_task(worker.start())

    relay_instance = relay["relay_instance"]
    for _ in range(50):
        if relay_instance.tunnel_ws is not None and not relay_instance.tunnel_ws.closed:
            break
        await asyncio.sleep(0.1)
    else:
        raise RuntimeError("Tunnel client did not connect within 5s")

    yield worker

    worker._running = False
    if relay_instance.tunnel_ws and not relay_instance.tunnel_ws.closed:
        await relay_instance.tunnel_ws.close()
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass
    if worker.session:
        await worker.session.close()


@pytest_asyncio.fixture
async def full_chain(relay, mock_llm, tunnel, x_server):
    """Full chain ready: x + mock_llm + relay + tunnel all connected."""
    yield {
        "relay": relay,
        "mock_llm": mock_llm,
        "tunnel": tunnel,
        "x_server": x_server,
        "relay_port": relay["port"],
        "mock_llm_port": mock_llm["port"],
    }


def _client_ssl_ctx():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


@pytest_asyncio.fixture
async def client(relay):
    """HTTP client that trusts self-signed certs."""
    connector = aiohttp.TCPConnector(ssl=_client_ssl_ctx())
    session = aiohttp.ClientSession(connector=connector)
    yield session
    await session.close()


@pytest_asyncio.fixture
async def http_client():
    """Plain HTTP client for talking to X (no TLS)."""
    session = aiohttp.ClientSession()
    yield session
    await session.close()
