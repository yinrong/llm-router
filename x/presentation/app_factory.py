"""Application factory: assembles repositories → services → relay → aiohttp app."""

from __future__ import annotations

import os

from aiohttp import web

from x import db as xdb
from x.application.audit_service import AuditService
from x.application.completion_service import CompletionService
from x.application.election_service import ElectionService
from x.application.group_service import GroupService
from x.application.heartbeat_service import HeartbeatService
from x.application.registration_service import RegistrationService
from x.infrastructure.repositories.jsonl_completion_repo import JsonlCompletionRepository
from x.infrastructure.repositories.sqlite_audit_repo import SqliteAuditRepository
from x.infrastructure.repositories.sqlite_client_repo import SqliteClientRepository
from x.infrastructure.repositories.sqlite_group_repo import SqliteGroupRepository
from x.presentation import handlers as h
from x.relay.multi_tenant import MultiTenantRelay


def create_app(
    *,
    db_path: str,
    releases_dir: str,
    x_base_url: str = "https://yinaisvr.duckdns.org",
    heartbeat_interval: int = 30,
    election_poll: int = 5,
) -> web.Application:
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    os.makedirs(releases_dir, exist_ok=True)

    completions_dir = os.path.join(os.path.dirname(db_path), "completions")

    # Infrastructure
    conn = xdb.connect(db_path)
    group_repo = SqliteGroupRepository(conn)
    client_repo = SqliteClientRepository(conn)
    audit_repo = SqliteAuditRepository(conn)

    # Application services
    group_svc = GroupService(group_repo)
    reg_svc = RegistrationService(group_repo, client_repo)
    hb_svc = HeartbeatService(client_repo)
    elect_svc = ElectionService(client_repo, group_repo)
    audit_svc = AuditService(audit_repo, group_repo)
    completion_svc = CompletionService(JsonlCompletionRepository(completions_dir))

    # Data-plane relay
    relay = MultiTenantRelay(
        group_repo=group_repo,
        audit_service=audit_svc,
        completion_service=completion_svc,
    )

    app = web.Application()
    app["services"] = {
        "group": group_svc,
        "registration": reg_svc,
        "heartbeat": hb_svc,
        "election": elect_svc,
        "audit": audit_svc,
        "client_repo": client_repo,
    }
    app["relay"] = relay
    app["config"] = {
        "x_base_url": x_base_url,
        "heartbeat_interval": heartbeat_interval,
        "election_poll": election_poll,
        "releases_dir": releases_dir,
        "db_path": db_path,
    }

    # Camouflage home page
    app.router.add_get("/", relay.handle_index)

    # Control-plane endpoints
    app.router.add_get("/healthz", h.healthz)
    app.router.add_post("/api/groups", h.create_group)
    app.router.add_get("/api/groups", h.list_groups)
    app.router.add_get(r"/api/groups/{group_id}", h.get_group)
    app.router.add_post("/api/register/b", h.register_b)
    app.router.add_post("/api/register/c", h.register_c)
    app.router.add_post("/api/heartbeat", h.heartbeat)
    app.router.add_post(r"/api/elect/{group_id}", h.elect)
    app.router.add_post("/api/audit", h.post_audit)
    app.router.add_get(r"/api/version/{role}", h.get_version)
    app.router.add_get(r"/api/download/{role}/{version}", h.download_release)
    app.router.add_get("/install/b.sh", h.install_b)
    app.router.add_get("/install/c.sh", h.install_c)
    app.router.add_get("/install/a.sh", h.install_a)
    app.router.add_get("/install/a.ps1", h.install_a_ps1)

    # Data-plane endpoints
    app.router.add_get("/ws/notifications", relay.handle_websocket)
    app.router.add_route("*", r"/g/{group_id}/{path:.*}", relay.handle_api)

    # Catch-all camouflage (must be last)
    app.router.add_route("*", r"/{path:.*}", relay.handle_catch_all)

    async def _close_db(app):
        try:
            app["config"]["_conn"].close()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass

    app.on_cleanup.append(_close_db)
    return app
