"""End-to-end tests for the full A→B→C→LLM chain."""

import asyncio
import json

import aiohttp
import pytest
import pytest_asyncio
from aiohttp import web


TEST_TUNNEL_SECRET = "tun-test-secret-for-e2e"
TEST_GROUP_ID = "13800138000_test"


async def test_static_index(x_server, client):
    """GET / returns the static index page."""
    port = x_server["port"]
    async with client.get(f"http://127.0.0.1:{port}/") as resp:
        assert resp.status == 200
        text = await resp.text()
        assert "<html" in text.lower() or "welcome" in text.lower()


async def test_unknown_path_returns_index(x_server, client):
    """GET /random/path returns static page (catch-all)."""
    port = x_server["port"]
    async with client.get(f"http://127.0.0.1:{port}/some/random/path") as resp:
        assert resp.status == 200
        text = await resp.text()
        assert "<html" in text.lower() or "welcome" in text.lower()


async def test_tunnel_auth_reject(x_server):
    """WebSocket with wrong cookie gets 404 (endpoint not revealed)."""
    port = x_server["port"]
    session = aiohttp.ClientSession()
    try:
        with pytest.raises(aiohttp.WSServerHandshakeError) as exc_info:
            async with session.ws_connect(
                f"ws://127.0.0.1:{port}/ws/notifications",
                headers={"Cookie": "_sid=wrong-secret"},
            ):
                pass
        assert exc_info.value.status == 404
    finally:
        await session.close()


async def test_no_tunnel_502(x_server, client):
    """API request without tunnel connected returns 502."""
    port = x_server["port"]
    relay = x_server["app"]["relay"]

    # Ensure no tunnel is connected for this group
    ws = relay.tunnels.get(TEST_GROUP_ID)
    if ws and not ws.closed:
        await ws.close()
        await asyncio.sleep(0.1)

    async with client.post(
        f"http://127.0.0.1:{port}/g/{TEST_GROUP_ID}/anthropic/v1/messages",
        json={"model": "test", "messages": [{"role": "user", "content": "hi"}]},
        headers={"content-type": "application/json"},
    ) as resp:
        assert resp.status == 502
        body = await resp.json()
        assert body["error"]["message"] == "service unavailable"


async def test_non_stream(full_chain, client):
    """Full chain non-stream request returns correct response."""
    port = full_chain["x_port"]

    async with client.post(
        f"http://127.0.0.1:{port}/g/{TEST_GROUP_ID}/v1/chat/completions",
        json={
            "model": "test-model",
            "messages": [{"role": "user", "content": "hello e2e"}],
        },
        headers={
            "content-type": "application/json",
            "x-api-key": "sk-test-key",
        },
    ) as resp:
        assert resp.status == 200
        body = await resp.json()
        assert "choices" in body
        content = body["choices"][0]["message"]["content"]
        assert "hello e2e" in content


async def test_stream(full_chain, client):
    """Full chain stream request returns SSE event stream."""
    port = full_chain["x_port"]

    async with client.post(
        f"http://127.0.0.1:{port}/g/{TEST_GROUP_ID}/v1/chat/completions",
        json={
            "model": "test-model",
            "messages": [{"role": "user", "content": "hello stream"}],
            "stream": True,
        },
        headers={
            "content-type": "application/json",
            "x-api-key": "sk-test-key",
        },
    ) as resp:
        assert resp.status == 200
        assert "text/event-stream" in resp.headers.get("Content-Type", "")

        chunks = []
        async for line in resp.content:
            decoded = line.decode().strip()
            if decoded:
                chunks.append(decoded)

        # Should have data chunks and end with [DONE]
        assert len(chunks) > 0
        assert any("[DONE]" in c for c in chunks)


async def test_header_allowlist(full_chain, client):
    """Custom headers are NOT forwarded to upstream LLM."""
    port = full_chain["x_port"]
    mock_llm = full_chain["mock_llm"]
    received = mock_llm["received_headers"]
    received.clear()

    async with client.post(
        f"http://127.0.0.1:{port}/g/{TEST_GROUP_ID}/v1/chat/completions",
        json={
            "model": "test-model",
            "messages": [{"role": "user", "content": "header test"}],
        },
        headers={
            "content-type": "application/json",
            "x-api-key": "sk-test-key",
            "X-Custom-Secret": "should-not-pass",
            "X-Internal-Debug": "also-blocked",
        },
    ) as resp:
        assert resp.status == 200
        await resp.json()

    # Check that custom headers did not reach mock LLM
    assert len(received) > 0
    last_headers = received[-1]
    header_keys_lower = [k.lower() for k in last_headers.keys()]
    assert "x-custom-secret" not in header_keys_lower
    assert "x-internal-debug" not in header_keys_lower
    # But allowed headers should pass
    assert "x-api-key" in header_keys_lower


async def test_concurrent_requests(full_chain, client):
    """Multiple concurrent requests each get correct responses."""
    port = full_chain["x_port"]

    async def make_request(i):
        async with client.post(
            f"http://127.0.0.1:{port}/g/{TEST_GROUP_ID}/v1/chat/completions",
            json={
                "model": "test-model",
                "messages": [{"role": "user", "content": f"concurrent-{i}"}],
            },
            headers={"content-type": "application/json"},
        ) as resp:
            assert resp.status == 200
            body = await resp.json()
            content = body["choices"][0]["message"]["content"]
            assert f"concurrent-{i}" in content
            return content

    results = await asyncio.gather(*[make_request(i) for i in range(5)])
    assert len(results) == 5
    # Each response should be unique
    assert len(set(results)) == 5


async def test_tunnel_disconnect_reconnect(x_server, mock_llm, client):
    """After tunnel disconnects, requests fail; after reconnect, they succeed."""
    import importlib
    import _server

    port = x_server["port"]
    relay = x_server["app"]["relay"]

    # Start tunnel
    importlib.reload(_server)
    worker = _server.Worker()
    task = asyncio.create_task(worker.start())

    for _ in range(50):
        if TEST_GROUP_ID in relay.tunnels and not relay.tunnels[TEST_GROUP_ID].closed:
            break
        await asyncio.sleep(0.1)

    # Verify working
    async with client.post(
        f"http://127.0.0.1:{port}/g/{TEST_GROUP_ID}/v1/chat/completions",
        json={"model": "test", "messages": [{"role": "user", "content": "before disconnect"}]},
        headers={"content-type": "application/json"},
    ) as resp:
        assert resp.status == 200

    # Disconnect tunnel
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

    await asyncio.sleep(0.5)

    # Request should fail
    async with client.post(
        f"http://127.0.0.1:{port}/g/{TEST_GROUP_ID}/v1/chat/completions",
        json={"model": "test", "messages": [{"role": "user", "content": "during disconnect"}]},
        headers={"content-type": "application/json"},
    ) as resp:
        assert resp.status == 502

    # Reconnect
    importlib.reload(_server)
    worker2 = _server.Worker()
    task2 = asyncio.create_task(worker2.start())

    for _ in range(50):
        if TEST_GROUP_ID in relay.tunnels and not relay.tunnels[TEST_GROUP_ID].closed:
            break
        await asyncio.sleep(0.1)

    # Request should work again
    async with client.post(
        f"http://127.0.0.1:{port}/g/{TEST_GROUP_ID}/v1/chat/completions",
        json={"model": "test", "messages": [{"role": "user", "content": "after reconnect"}]},
        headers={"content-type": "application/json"},
    ) as resp:
        assert resp.status == 200

    # Cleanup
    worker2._running = False
    ws2 = relay.tunnels.get(TEST_GROUP_ID)
    if ws2 and not ws2.closed:
        await ws2.close()
    task2.cancel()
    try:
        await task2
    except (asyncio.CancelledError, Exception):
        pass
    if worker2.session:
        await worker2.session.close()


async def test_upstream_unreachable(x_server, client):
    """Request when upstream LLM is unreachable returns 502 proxy_error."""
    import importlib
    import os
    import socket
    import _server

    port = x_server["port"]
    relay = x_server["app"]["relay"]

    # Find a port with nothing listening
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    dead_port = sock.getsockname()[1]
    sock.close()

    # Reconfigure C to point to unreachable server
    os.environ["INTERNAL_LLM_BASE"] = f"http://127.0.0.1:{dead_port}"

    import config
    importlib.reload(config)
    importlib.reload(_server)

    worker = _server.Worker()
    task = asyncio.create_task(worker.start())

    for _ in range(100):
        if TEST_GROUP_ID in relay.tunnels and not relay.tunnels[TEST_GROUP_ID].closed:
            break
        await asyncio.sleep(0.1)
    assert TEST_GROUP_ID in relay.tunnels and not relay.tunnels[TEST_GROUP_ID].closed, "Tunnel failed to connect"

    # C can't connect to upstream → proxy_error 502
    async with client.post(
        f"http://127.0.0.1:{port}/g/{TEST_GROUP_ID}/v1/chat/completions",
        json={"model": "test", "messages": [{"role": "user", "content": "unreachable test"}]},
        headers={"content-type": "application/json"},
        timeout=aiohttp.ClientTimeout(total=10),
    ) as resp:
        assert resp.status == 502
        body = await resp.json()
        assert body["error"]["type"] == "proxy_error"

    # Cleanup
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
