from x.application.group_service import GroupService
from x.application.registration_service import RegistrationService
from x.application.election_service import ElectionService
from x.infrastructure.repositories.in_memory.group_repo import InMemoryGroupRepository
from x.infrastructure.repositories.in_memory.client_repo import InMemoryClientRepository


def setup():
    group_repo = InMemoryGroupRepository()
    client_repo = InMemoryClientRepository()
    group_svc = GroupService(group_repo)
    reg_svc = RegistrationService(group_repo, client_repo)
    elect_svc = ElectionService(client_repo, group_repo)
    return group_svc, reg_svc, elect_svc, client_repo


GID = "13800000001_elect"


def test_single_c_becomes_active():
    group_svc, reg_svc, elect_svc, _ = setup()
    group_svc.create_group("13800000001", "elect")
    reg_svc.register_c(GID, "c1")
    result = elect_svc.claim_active(GID, "c1", election_poll=5, ts=1010)
    assert result["active"] is True
    assert result["active_client_id"] == "c1"


def test_unknown_client_returns_error():
    group_svc, _, elect_svc, _ = setup()
    group_svc.create_group("13800000001", "elect")
    result = elect_svc.claim_active(GID, "ghost", election_poll=5, ts=1010)
    assert result["active"] is False
    assert result.get("error") == "unknown_client"


def test_two_cs_first_registered_wins():
    group_svc, reg_svc, elect_svc, _ = setup()
    group_svc.create_group("13800000001", "elect")
    reg_svc.register_c(GID, "c1", ts=100)
    reg_svc.register_c(GID, "c2", ts=200)
    # 两者都最近有心跳（ts=1010），c1 注册更早应赢
    elect_svc.claim_active(GID, "c1", election_poll=5, ts=1010)
    result = elect_svc.claim_active(GID, "c2", election_poll=5, ts=1010)
    assert result["active_client_id"] == "c1"
    assert result["active"] is False


def test_stale_active_gets_replaced():
    group_svc, reg_svc, elect_svc, client_repo = setup()
    group_svc.create_group("13800000001", "elect")
    reg_svc.register_c(GID, "c1", ts=100)
    reg_svc.register_c(GID, "c2", ts=200)
    # c1 成为 active（ts=500）
    elect_svc.claim_active(GID, "c1", election_poll=5, ts=500)
    # 此后 c1 心跳停止，c2 在 ts=1010 发起选举（stale_threshold=1000）
    # c1.last_heartbeat=500 < 1000，c1 stale，c2 应接管
    result = elect_svc.claim_active(GID, "c2", election_poll=5, ts=1010)
    assert result["active_client_id"] == "c2"
    assert result["active"] is True


def test_election_updates_heartbeat():
    group_svc, reg_svc, elect_svc, client_repo = setup()
    group_svc.create_group("13800000001", "elect")
    reg_svc.register_c(GID, "c1", ts=100)
    elect_svc.claim_active(GID, "c1", election_poll=5, ts=1010)
    c = client_repo.get("c1")
    assert c.last_heartbeat == 1010
