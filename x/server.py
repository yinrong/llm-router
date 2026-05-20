"""X — central coordinator HTTP server."""

from __future__ import annotations

import json
import logging
import os
import secrets
import sqlite3
import time

from aiohttp import web

from x import VERSION
from x import db as xdb
from x import election as xelection
from x import relay as xrelay
from x import scripts as xscripts
from x import version as xver

log = logging.getLogger("x")


def _json_error(message: str, status: int) -> web.Response:
    return web.json_response({"error": {"message": message}}, status=status)


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

    conn: sqlite3.Connection = request.app["db"]
    try:
        info = xdb.create_group(conn, phone, suffix)
    except ValueError as e:
        if "already exists" in str(e):
            return _json_error(str(e), 409)
        return _json_error(str(e), 400)
    return web.json_response(info, status=201)


async def list_groups(request: web.Request) -> web.Response:
    phone = request.query.get("phone")
    conn: sqlite3.Connection = request.app["db"]
    groups = xdb.list_groups_by_phone(conn, phone)
    return web.json_response({"groups": groups})


async def get_group(request: web.Request) -> web.Response:
    group_id = request.match_info["group_id"]
    conn: sqlite3.Connection = request.app["db"]
    g = xdb.get_group(conn, group_id)
    if not g:
        return _json_error("group not found", 404)
    clients = conn.execute(
        "SELECT client_id, role, hostname, version, registered_at, last_heartbeat, is_active FROM clients WHERE group_id=?",
        (group_id,),
    ).fetchall()
    g["clients"] = [dict(r) for r in clients]
    return web.json_response(g)


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

    conn: sqlite3.Connection = request.app["db"]
    g = xdb.get_group(conn, group_id)
    if not g:
        return _json_error("unknown group_id", 404)

    try:
        xdb.upsert_client(conn, client_id=client_id, group_id=group_id, role="B", hostname=hostname, version=version)
    except ValueError as e:
        return _json_error(str(e), 400)
    if public_addr and port:
        xdb.update_b_addr(conn, group_id, public_addr, port)
    return web.json_response({
        "tunnel_secret": g["tunnel_secret"],
        "heartbeat_interval": request.app["config"]["heartbeat_interval"],
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

    conn: sqlite3.Connection = request.app["db"]
    g = xdb.get_group(conn, group_id)
    if not g:
        return _json_error("unknown group_id", 404)
    try:
        xdb.upsert_client(conn, client_id=client_id, group_id=group_id, role="C", hostname=hostname, version=version)
    except ValueError as e:
        return _json_error(str(e), 400)
    return web.json_response({
        "relay_addr": g.get("b_addr") or "",
        "relay_port": g.get("b_port") or 0,
        "tunnel_secret": g["tunnel_secret"],
        "heartbeat_interval": request.app["config"]["heartbeat_interval"],
        "election_poll": request.app["config"]["election_poll"],
    })


async def heartbeat(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        return _json_error("invalid json", 400)
    client_id = body.get("client_id") or ""
    if not client_id:
        return _json_error("client_id required", 400)
    conn: sqlite3.Connection = request.app["db"]
    ok = xdb.heartbeat(conn, client_id)
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
    conn: sqlite3.Connection = request.app["db"]
    poll = request.app["config"]["election_poll"]
    result = xelection.claim_active(conn, group_id, client_id, election_poll=poll)
    if result.get("error"):
        return _json_error(result["error"], 404)
    return web.json_response(result)


async def post_audit(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        return _json_error("invalid json", 400)
    group_id = body.get("group_id") or ""
    b_client_id = body.get("b_client_id") or ""
    events = body.get("events") or []
    if not group_id:
        return _json_error("group_id required", 400)
    if not isinstance(events, list):
        return _json_error("events must be a list", 400)
    conn: sqlite3.Connection = request.app["db"]
    if not xdb.get_group(conn, group_id):
        return _json_error("unknown group_id", 404)
    n = xdb.insert_audit_events(conn, group_id, b_client_id, events)
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
    # B is now part of X — no separate install needed.
    msg = "# B is now co-located with X.\n# Users only need to install C.\n# See /install/c.sh\n"
    return web.Response(text=msg, content_type="text/x-shellscript")


async def install_c(request: web.Request) -> web.Response:
    return web.Response(text=_render_install("c.sh.tmpl", request), content_type="text/x-shellscript")


async def install_a(request: web.Request) -> web.Response:
    return web.Response(text=_render_install("a.sh.tmpl", request), content_type="text/x-shellscript")


async def install_a_ps1(request: web.Request) -> web.Response:
    return web.Response(text=_render_install("a.ps1.tmpl", request), content_type="text/plain")


def create_app(*, db_path: str, releases_dir: str, x_base_url: str = "https://yinaisvr.duckdns.org",
               heartbeat_interval: int = 30, election_poll: int = 5) -> web.Application:
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    os.makedirs(releases_dir, exist_ok=True)
    conn = xdb.connect(db_path)
    relay = xrelay.MultiTenantRelay(db=conn)

    app = web.Application()
    app["db"] = conn
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
    app.router.add_get("/healthz", healthz)
    app.router.add_post("/api/groups", create_group)
    app.router.add_get("/api/groups", list_groups)
    app.router.add_get(r"/api/groups/{group_id}", get_group)
    app.router.add_post("/api/register/b", register_b)
    app.router.add_post("/api/register/c", register_c)
    app.router.add_post("/api/heartbeat", heartbeat)
    app.router.add_post(r"/api/elect/{group_id}", elect)
    app.router.add_post("/api/audit", post_audit)
    app.router.add_get(r"/api/version/{role}", get_version)
    app.router.add_get(r"/api/download/{role}/{version}", download_release)
    app.router.add_get("/install/b.sh", install_b)
    app.router.add_get("/install/c.sh", install_c)
    app.router.add_get("/install/a.sh", install_a)
    app.router.add_get("/install/a.ps1", install_a_ps1)

    # Data-plane endpoints (relay: C tunnel + A API calls)
    app.router.add_get("/ws/notifications", relay.handle_websocket)
    app.router.add_route("*", r"/g/{group_id}/{path:.*}", relay.handle_api)

    # Catch-all camouflage (must be last)
    app.router.add_route("*", r"/{path:.*}", relay.handle_catch_all)

    async def _close_db(app):
        try:
            app["db"].close()
        except Exception:
            pass

    app.on_cleanup.append(_close_db)
    return app


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    home = os.environ.get("LLMROUTER_HOME", os.path.expanduser("~/.llmrouter"))
    db_path = os.environ.get("X_DB_PATH", os.path.join(home, "data", "x.sqlite"))
    releases_dir = os.environ.get("X_RELEASES_DIR", os.path.join(home, "releases"))
    host = os.environ.get("X_HOST", "0.0.0.0")
    port = int(os.environ.get("X_PORT", "8000"))
    x_base_url = os.environ.get("X_BASE_URL", "https://yinaisvr.duckdns.org")

    app = create_app(db_path=db_path, releases_dir=releases_dir, x_base_url=x_base_url)
    web.run_app(app, host=host, port=port)


if __name__ == "__main__":
    main()
