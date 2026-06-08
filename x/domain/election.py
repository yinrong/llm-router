from __future__ import annotations
from dataclasses import dataclass
from typing import List, Optional


@dataclass(frozen=True)
class CandidateSnapshot:
    client_id: str
    last_heartbeat: int
    registered_at: int
    is_active: bool
    active_since: Optional[int]


@dataclass(frozen=True)
class ElectionDecision:
    winner_id: str
    winner_since: int
    took_over: bool


def decide_election(
    requester_id: str,
    candidates: List[CandidateSnapshot],
    *,
    now_ts: int,
    stale_threshold: int,
) -> ElectionDecision:
    """
    纯函数选主：给定候选人快照，返回应成为 active 的 winner。
    candidates 必须已包含 requester（heartbeat 已在外部更新）。
    """
    active = next((c for c in candidates if c.is_active), None)

    if active is None:
        alive = [c for c in candidates if c.last_heartbeat >= stale_threshold]
        if not alive:
            winner_id = requester_id
        else:
            winner = min(alive, key=lambda c: (c.registered_at, c.client_id))
            winner_id = winner.client_id
        return ElectionDecision(winner_id=winner_id, winner_since=now_ts, took_over=True)

    if active.last_heartbeat < stale_threshold:
        return ElectionDecision(winner_id=requester_id, winner_since=now_ts, took_over=True)

    return ElectionDecision(
        winner_id=active.client_id,
        winner_since=active.active_since or now_ts,
        took_over=False,
    )
