# C self-replication (deferred)

For a future round. The goal is letting an active C copy itself to other
hosts on the same LAN, without SSH credentials, so the group keeps a hot
standby even if the primary host dies.

Sketch of the design:

1. C broadcasts an mDNS/UDP "I am llmrouter group <gid>" record.
2. A peer-discovery daemon on candidate hosts joins the group via a
   pre-shared `replicate_token` issued by X (`POST /api/groups/<gid>/replicate-token`).
3. The active C streams its tarball + `.env` (minus secrets) to the peer over
   the LAN, the peer extracts under `~/.llmrouter/c/`, registers with X under
   a fresh `client_id`, and joins the election.

Open questions deferred:

- How to bootstrap the per-host user (`useradd`?) without root?
- Trust model — `replicate_token` is a one-shot capability, but timing
  attacks and LAN snooping still need consideration.
- Should mDNS be replaced with a small UDP rendezvous beacon to dodge mDNS
  filtering on enterprise wifi?

Until then, `c_replicate.maybe_replicate()` is a no-op.
