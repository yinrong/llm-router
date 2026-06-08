"""End-to-end tests for the full A→B→C→LLM chain."""

import asyncio
import json
import os

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
    import os
    from c.settings import CSettings
    from c.tunnel_worker import TunnelWorker

    port = x_server["port"]
    relay = x_server["app"]["relay"]

    def _make_worker():
        s = CSettings(
            x_base_url=x_server["url"],
            group_id=TEST_GROUP_ID,
            client_id=os.environ["CLIENT_ID_C"],
            tunnel_secret=TEST_TUNNEL_SECRET,
            internal_llm_base=f"http://127.0.0.1:{mock_llm['port']}",
            cache_dir=os.path.join(x_server["home"], "cache"),
        )
        return TunnelWorker(s)

    # Start tunnel
    worker = _make_worker()
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
    worker2 = _make_worker()
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
    import os
    import socket
    from c.settings import CSettings
    from c.tunnel_worker import TunnelWorker

    port = x_server["port"]
    relay = x_server["app"]["relay"]

    # Find a port with nothing listening
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    dead_port = sock.getsockname()[1]
    sock.close()

    # Create worker pointing to unreachable LLM
    settings = CSettings(
        x_base_url=x_server["url"],
        group_id=TEST_GROUP_ID,
        client_id=os.environ["CLIENT_ID_C"],
        tunnel_secret=TEST_TUNNEL_SECRET,
        internal_llm_base=f"http://127.0.0.1:{dead_port}",
        cache_dir=os.path.join(x_server["home"], "cache"),
    )
    worker = TunnelWorker(settings)
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


async def test_completion_log_non_stream(full_chain, client):
    """Non-stream request writes a JSONL completion record to disk."""
    port = full_chain["x_port"]
    home = full_chain["x_server"]["home"]

    async with client.post(
        f"http://127.0.0.1:{port}/g/{TEST_GROUP_ID}/v1/chat/completions",
        json={
            "model": "test-model",
            "messages": [{"role": "user", "content": "log me"}],
        },
        headers={"content-type": "application/json"},
    ) as resp:
        assert resp.status == 200

    await asyncio.sleep(0.1)

    completions_dir = os.path.join(home, "data", "completions")
    records = []
    for fname in sorted(os.listdir(completions_dir)):
        with open(os.path.join(completions_dir, fname)) as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))

    matched = [r for r in records if r.get("messages") == [{"role": "user", "content": "log me"}]]
    assert len(matched) >= 1
    r = matched[0]
    assert r["group_id"] == TEST_GROUP_ID
    assert r["model"] == "test-model"
    assert r["finish_reason"] != ""
    assert r["latency_ms"] >= 0


async def test_completion_log_stream(full_chain, client):
    """Stream request writes a JSONL completion record after stream_end."""
    port = full_chain["x_port"]
    home = full_chain["x_server"]["home"]

    async with client.post(
        f"http://127.0.0.1:{port}/g/{TEST_GROUP_ID}/v1/chat/completions",
        json={
            "model": "test-model",
            "messages": [{"role": "user", "content": "stream log me"}],
            "stream": True,
        },
        headers={"content-type": "application/json"},
    ) as resp:
        assert resp.status == 200
        async for _ in resp.content:
            pass

    await asyncio.sleep(0.1)

    completions_dir = os.path.join(home, "data", "completions")
    files = sorted(os.listdir(completions_dir))
    assert len(files) >= 1

    records = []
    for fname in files:
        with open(os.path.join(completions_dir, fname)) as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))

    stream_records = [
        r for r in records
        if r.get("messages") == [{"role": "user", "content": "stream log me"}]
    ]
    assert len(stream_records) >= 1
    r = stream_records[0]
    assert r["group_id"] == TEST_GROUP_ID
    assert r["model"] == "test-model"
