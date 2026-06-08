import pytest
from x.application.group_service import GroupService
from x.application.registration_service import RegistrationService
from x.infrastructure.repositories.in_memory.group_repo import InMemoryGroupRepository
from x.infrastructure.repositories.in_memory.client_repo import InMemoryClientRepository
from x.domain.client import ClientRole


def make_services():
    group_repo = InMemoryGroupRepository()
    client_repo = InMemoryClientRepository()
    group_svc = GroupService(group_repo)
    reg_svc = RegistrationService(group_repo, client_repo)
    return group_svc, reg_svc, client_repo


def test_register_b_returns_tunnel_secret():
    group_svc, reg_svc, _ = make_services()
    group_svc.create_group("13800000001", "t1", tunnel_secret="tun-test")
    result = reg_svc.register_b("13800000001_t1", "b-001",
                                public_addr="1.2.3.4", port=443, hostname="host")
    assert result["tunnel_secret"] == "tun-test"


def test_register_b_unknown_group_raises():
    _, reg_svc, _ = make_services()
    with pytest.raises(KeyError):
        reg_svc.register_b("13800000002_ghost", "b-001")


def test_register_b_updates_b_addr():
    group_svc, reg_svc, _ = make_services()
    group_svc.create_group("13800000003", "t3")
    reg_svc.register_b("13800000003_t3", "b-t3",
                       public_addr="5.6.7.8", port=9443)
    group = group_svc.get_group("13800000003_t3")
    assert group.b_addr == "5.6.7.8"
    assert group.b_port == 9443


def test_register_c_relay_addr_reflects_b():
    group_svc, reg_svc, _ = make_services()
    group_svc.create_group("13800000004", "t4", tunnel_secret="tun-t4")
    reg_svc.register_b("13800000004_t4", "b-t4",
                       public_addr="9.9.9.9", port=8443)
    result = reg_svc.register_c("13800000004_t4", "c-t4")
    assert result["relay_addr"] == "9.9.9.9"
    assert result["relay_port"] == 8443
    assert result["tunnel_secret"] == "tun-t4"


def test_register_c_no_b_gives_empty_addr():
    group_svc, reg_svc, _ = make_services()
    group_svc.create_group("13800000005", "t5", tunnel_secret="tun-t5")
    result = reg_svc.register_c("13800000005_t5", "c-t5")
    assert result["relay_addr"] == ""
    assert result["relay_port"] == 0


def test_register_b_creates_client():
    group_svc, reg_svc, client_repo = make_services()
    group_svc.create_group("13800000006", "t6")
    reg_svc.register_b("13800000006_t6", "b-t6", hostname="myhost")
    c = client_repo.get("b-t6")
    assert c is not None
    assert c.role == ClientRole.B
    assert c.hostname == "myhost"


def test_register_idempotent_updates_version():
    group_svc, reg_svc, client_repo = make_services()
    group_svc.create_group("13800000007", "t7")
    reg_svc.register_b("13800000007_t7", "b-t7", version="0.1")
    reg_svc.register_b("13800000007_t7", "b-t7", version="0.2")
    c = client_repo.get("b-t7")
    assert c.version == "0.2"
    # 只有一个 client
    clients = client_repo.list_by_group("13800000007_t7")
    assert len(clients) == 1
