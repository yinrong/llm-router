from x.domain.election import decide_election, CandidateSnapshot


def snap(cid, last_hb, registered_at=0, is_active=False, active_since=None):
    return CandidateSnapshot(cid, last_hb, registered_at, is_active, active_since)


# stale_threshold = now_ts - 2 * election_poll
# 使用 now_ts=1010, stale_threshold=990（election_poll=10）


def test_no_active_single_candidate_wins():
    d = decide_election("c1", [snap("c1", last_hb=1000)], now_ts=1010, stale_threshold=990)
    assert d.winner_id == "c1"
    assert d.took_over is True
    assert d.winner_since == 1010


def test_no_active_earliest_registered_wins():
    candidates = [
        snap("c2", last_hb=1000, registered_at=200),
        snap("c1", last_hb=1000, registered_at=100),
    ]
    d = decide_election("c2", candidates, now_ts=1010, stale_threshold=990)
    assert d.winner_id == "c1"
    assert d.took_over is True


def test_tie_break_by_client_id():
    candidates = [
        snap("c2", last_hb=1000, registered_at=100),
        snap("c1", last_hb=1000, registered_at=100),
    ]
    d = decide_election("c2", candidates, now_ts=1010, stale_threshold=990)
    assert d.winner_id == "c1"  # 字典序 c1 < c2


def test_active_healthy_stays():
    candidates = [
        snap("c1", last_hb=1005, is_active=True, active_since=800),
        snap("c2", last_hb=1000),
    ]
    d = decide_election("c2", candidates, now_ts=1010, stale_threshold=990)
    assert d.winner_id == "c1"
    assert d.took_over is False
    assert d.winner_since == 800


def test_active_stale_requester_takes_over():
    candidates = [
        snap("c1", last_hb=900, is_active=True, active_since=800),
        snap("c2", last_hb=1000),
    ]
    d = decide_election("c2", candidates, now_ts=1010, stale_threshold=995)
    assert d.winner_id == "c2"
    assert d.took_over is True


def test_active_stale_and_requester_stale_requester_still_wins():
    # 所有候选人都过期，但 requester 仍被选（failover 语义）
    candidates = [
        snap("c1", last_hb=900, is_active=True, active_since=800),
        snap("c2", last_hb=850),
    ]
    d = decide_election("c2", candidates, now_ts=1010, stale_threshold=995)
    assert d.winner_id == "c2"
    assert d.took_over is True


def test_no_alive_candidates_requester_wins():
    # 无 active，也无活跃候选人（全部 stale）
    candidates = [snap("c1", last_hb=800)]
    d = decide_election("c1", candidates, now_ts=1010, stale_threshold=995)
    assert d.winner_id == "c1"
    assert d.took_over is True
