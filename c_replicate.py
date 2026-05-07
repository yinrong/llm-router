"""Self-replication stub for C — see docs/replication.md.

Future work: allow an active C to replicate itself to peer machines on the
same LAN without SSH credentials, for redundancy. Out of scope for v0.
"""

from __future__ import annotations


async def maybe_replicate(group_id: str, x_client) -> None:  # noqa: ARG001
    return None
