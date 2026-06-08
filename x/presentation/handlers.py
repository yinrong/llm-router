"""Presentation layer: aiohttp HTTP handlers.

All handlers receive services via request.app["services"] dict.
No direct database access here.
"""

from __future__ import annotations

import logging
import os

from aiohttp import web

from x import VERSION
from x import scripts as xscripts
from x import version as xver
from x.application.audit_service import AuditService
from x.application.election_service import ElectionService
from x.application.group_service import GroupService
from x.application.heartbeat_service import HeartbeatService
from x.application.registration_service import RegistrationService

log = logging.getLogger("x")


def _json_error(message: str, status: int) -> web.Response:
    return web.json_response({"error": {"message": message}}, status=status)


def _services(request: web.Request) -> dict:
    return request.app["services"]


async def healthz(request: web.Request) -> web.Response:
    return web.json_response({"ok": True, "version": VERSION})


async def create_group(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        return _json_error("invalid json", 400)
    phone = (body.get("phone") or "").strip()
    suffix = (body.get("suffix") or "").strip()
    if not phone or not suffix:
        return _json_error("phone and suffix required", 400)

    svc: GroupService = _services(request)["group"]
    try:
        group = svc.create_group(phone, suffix)
    except ValueError as e:
        if "already exists" in str(e):
            return _json_error(str(e), 409)
        return _json_error(str(e), 400)
    return web.json_response(
        {
            "group_id": group.group_id.value,
            "tunnel_secret": group.tunnel_secret,
            "created_at": group.created_at,
        },
        status=201,
    )


async def list_groups(request: web.Request) -> web.Response:
    phone = request.query.get("phone")
    svc: GroupService = _services(request)["group"]
    client_repo = _services(request)["client_repo"]
    groups = svc.list_groups(phone)
    out = []
    for g in groups:
        gid = g.group_id.value
        candidates = client_repo.list_by_group(gid)
        from x.domain.client import ClientRole
        c_clients = [c for c in candidates if c.role == ClientRole.C]
        out.append({
            "group_id": gid,
            "phone": g.phone,
            "suffix": g.suffix,
            "tunnel_secret": g.tunnel_secret,
            "created_at": g.created_at,
            "b_addr": g.b_addr,
            "b_port": g.b_port,
            "b_last_seen": g.b_last_seen,
            "c_active_count": sum(1 for c in c_clients if c.is_active),
            "c_total_count": len(c_clients),
        })
    return web.json_response({"groups": out})


async def get_group(request: web.Request) -> web.Response:
    group_id = request.match_info["group_id"]
    svc: GroupService = _services(request)["group"]
    client_repo = _services(request)["client_repo"]
    g = svc.get_group(group_id)
    if not g:
        return _json_error("group not found", 404)
    clients = client_repo.list_by_group(group_id)
    return web.json_response({
        "group_id": g.group_id.value,
        "phone": g.phone,
        "suffix": g.suffix,
        "tunnel_secret": g.tunnel_secret,
        "created_at": g.created_at,
        "b_addr": g.b_addr,
        "b_port": g.b_port,
        "b_last_seen": g.b_last_seen,
        "clients": [
            {
                "client_id": c.client_id,
                "role": c.role.value,
                "hostname": c.hostname,
                "version": c.version,
                "registered_at": c.registered_at,
                "last_heartbeat": c.last_heartbeat,
                "is_active": c.is_active,
            }
            for c in clients
        ],
    })


async def register_b(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        return _json_error("invalid json", 400)
    group_id = body.get("group_id") or ""
    client_id = body.get("client_id") or ""
    public_addr = body.get("public_addr") or ""
    port = int(body.get("port") or 0)
    version = body.get("version") or ""
    hostname = body.get("hostname") or ""
    if not (group_id and client_id):
        return _json_error("group_id and client_id required", 400)

    svc: RegistrationService = _services(request)["registration"]
    cfg = request.app["config"]
    try:
        result = svc.register_b(
            group_id, client_id,
            public_addr=public_addr, port=port,
            version=version, hostname=hostname,
        )
    except KeyError:
        return _json_error("unknown group_id", 404)
    return web.json_response({
        "tunnel_secret": result["tunnel_secret"],
        "heartbeat_interval": cfg["heartbeat_interval"],
    })


async def register_c(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        return _json_error("invalid json", 400)
    group_id = body.get("group_id") or ""
    client_id = body.get("client_id") or ""
    version = body.get("version") or ""
    hostname = body.get("hostname") or ""
    if not (group_id and client_id):
        return _json_error("group_id and client_id required", 400)

    svc: RegistrationService = _services(request)["registration"]
    cfg = request.app["config"]
    try:
        result = svc.register_c(
            group_id, client_id, version=version, hostname=hostname,
        )
    except KeyError:
        return _json_error("unknown group_id", 404)
    return web.json_response({
        "relay_addr": result["relay_addr"],
        "relay_port": result["relay_port"],
        "tunnel_secret": result["tunnel_secret"],
        "heartbeat_interval": cfg["heartbeat_interval"],
        "election_poll": cfg["election_poll"],
    })


async def heartbeat(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        return _json_error("invalid json", 400)
    client_id = body.get("client_id") or ""
    if not client_id:
        return _json_error("client_id required", 400)
    svc: HeartbeatService = _services(request)["heartbeat"]
    ok = svc.heartbeat(client_id)
    if not ok:
        return _json_error("unknown client_id", 404)
    return web.json_response({"ok": True})


async def elect(request: web.Request) -> web.Response:
    group_id = request.match_info["group_id"]
    try:
        body = await request.json()
    except Exception:
        return _json_error("invalid json", 400)
    client_id = body.get("client_id") or ""
    if not client_id:
        return _json_error("client_id required", 400)
    svc: ElectionService = _services(request)["election"]
    poll = request.app["config"]["election_poll"]
    result = svc.claim_active(group_id, client_id, election_poll=poll)
    if result.get("error"):
        return _json_error(result["error"], 404)
    return web.json_response(result)


async def post_audit(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        return _json_error("invalid json", 400)
    group_id = body.get("group_id") or ""
    b_client_id = body.get("b_client_id") or None
    events = body.get("events") or []
    if not group_id:
        return _json_error("group_id required", 400)
    if not isinstance(events, list):
        return _json_error("events must be a list", 400)
    svc: AuditService = _services(request)["audit"]
    try:
        n = svc.post_events(group_id, b_client_id, events)
    except KeyError:
        return _json_error("unknown group_id", 404)
    return web.json_response({"accepted": n})


async def get_version(request: web.Request) -> web.Response:
    role = request.match_info["role"]
    if role not in ("b", "c", "a"):
        return _json_error("unknown role", 404)
    info = xver.tarball_info(role, request.app["config"]["releases_dir"])
    return web.json_response({
        "version": info["version"],
        "sha256": info["sha256"],
        "url": info["url_path"],
        "available": info["exists"],
    })


async def download_release(request: web.Request) -> web.StreamResponse:
    role = request.match_info["role"]
    version = request.match_info["version"]
    if role not in ("b", "c", "a"):
        return _json_error("unknown role", 404)
    if version == "latest":
        version = xver.latest_version(role)
    fname = f"{role}-{version}.tgz"
    fpath = os.path.join(request.app["config"]["releases_dir"], fname)
    if not os.path.exists(fpath):
        return _json_error("release not found", 404)
    return web.FileResponse(fpath, headers={"Content-Type": "application/gzip"})


def _render_install(name: str, request: web.Request) -> str:
    base = request.app["config"]["x_base_url"]
    return xscripts.render(
        name,
        X_BASE_URL_DEFAULT=base,
        GROUP_ID_DEFAULT=request.query.get("group_id", ""),
    )


async def install_b(request: web.Request) -> web.Response:
    msg = "# B is now co-located with X.\n# Users only need to install C.\n# See /install/c.sh\n"
    return web.Response(text=msg, content_type="text/x-shellscript")


async def install_c(request: web.Request) -> web.Response:
    return web.Response(text=_render_install("c.sh.tmpl", request), content_type="text/x-shellscript")


async def install_a(request: web.Request) -> web.Response:
    return web.Response(text=_render_install("a.sh.tmpl", request), content_type="text/x-shellscript")


async def install_a_ps1(request: web.Request) -> web.Response:
    return web.Response(text=_render_install("a.ps1.tmpl", request), content_type="text/plain")
