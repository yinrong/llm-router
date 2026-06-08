from c.settings import CSettings


def make(x_base_url="http://127.0.0.1:8000", tunnel_secret="tun-test"):
    return CSettings(
        x_base_url=x_base_url,
        group_id="13800000001_x",
        client_id="c1",
        tunnel_secret=tunnel_secret,
        internal_llm_base="http://localhost:9000",
        cache_dir="/tmp",
    )


def test_ws_url_https_becomes_wss():
    s = make(x_base_url="https://example.com")
    assert s.ws_url == "wss://example.com/ws/notifications"


def test_ws_url_http_becomes_ws():
    s = make(x_base_url="http://127.0.0.1:8000")
    assert s.ws_url == "ws://127.0.0.1:8000/ws/notifications"


def test_with_tunnel_secret_returns_new_instance():
    s1 = make(tunnel_secret="old")
    s2 = s1.with_tunnel_secret("new")
    assert s1.tunnel_secret == "old"
    assert s2.tunnel_secret == "new"
    assert s1 is not s2


def test_with_tunnel_secret_preserves_other_fields():
    s1 = make()
    s2 = s1.with_tunnel_secret("new")
    assert s2.group_id == s1.group_id
    assert s2.x_base_url == s1.x_base_url
    assert s2.internal_llm_base == s1.internal_llm_base


def test_frozen():
    s = make()
    import pytest
    with pytest.raises(Exception):
        s.tunnel_secret = "modified"


def test_defaults():
    s = make()
    assert s.heartbeat_min == 20
    assert s.heartbeat_max == 40
    assert s.reconnect_base == 5
    assert s.reconnect_max == 60
    assert s.request_timeout == 120
    assert s.ws_path == "/ws/notifications"
