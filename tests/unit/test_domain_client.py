import pytest
from x.domain.client import Client, ClientRole


def test_client_role_enum():
    assert ClientRole.B.value == "B"
    assert ClientRole.C.value == "C"
    assert ClientRole.A.value == "A"


def test_client_role_from_str():
    assert ClientRole("B") == ClientRole.B


def test_client_frozen():
    c = Client(
        client_id="c1", group_id="13800000001_x",
        role=ClientRole.C, hostname="host", version="0.1",
        registered_at=1000, last_heartbeat=1000,
    )
    with pytest.raises(Exception):
        c.client_id = "other"


def test_client_defaults():
    c = Client(
        client_id="b1", group_id="13800000001_x",
        role=ClientRole.B, hostname=None, version=None,
        registered_at=100, last_heartbeat=100,
    )
    assert c.is_active is False
    assert c.active_since is None
