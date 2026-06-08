from __future__ import annotations
import time
from typing import Optional

from x.domain.election import decide_election
from x.infrastructure.repositories.base import ClientRepository, GroupRepository

ACTIVE_TAKEOVER_FACTOR = 2


class ElectionService:
    def __init__(self, client_repo: ClientRepository, group_repo: GroupRepository):
        self._clients = client_repo
        self._groups = group_repo

    def claim_active(
        self,
        group_id: str,
        client_id: str,
        *,
        election_poll: int = 5,
        ts: Optional[int] = None,
    ) -> dict:
        ts = ts or int(time.time())
        stale_threshold = ts - ACTIVE_TAKEOVER_FACTOR * max(election_poll, 1)

        ok = self._clients.update_heartbeat(client_id, ts)
        if not ok:
            return {
                "active": False,
                "active_client_id": None,
                "active_since": None,
                "error": "unknown_client",
            }

        candidates = self._clients.get_candidates(group_id)
        decision = decide_election(
            client_id, candidates, now_ts=ts, stale_threshold=stale_threshold
        )

        if decision.took_over:
            self._clients.clear_active(group_id)
            self._clients.set_active(group_id, decision.winner_id, decision.winner_since)

        return {
            "active": decision.winner_id == client_id,
            "active_client_id": decision.winner_id,
            "active_since": decision.winner_since,
        }
