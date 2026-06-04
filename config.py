import os

# 自动加载 .env 文件
_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(_env_path):
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _, _v = _line.partition("=")
                os.environ.setdefault(_k.strip(), _v.strip())

RELAY_HOST = os.environ.get("RELAY_HOST", "0.0.0.0")
RELAY_PORT = int(os.environ.get("RELAY_PORT", "443"))

# B 的公网地址（C 和 A 连这里）
RELAY_ADDR = os.environ.get("RELAY_ADDR", "127.0.0.1")

TUNNEL_SECRET = os.environ.get("TUNNEL_SECRET", "tun-llmrouter-default-secret-change-me")

# WebSocket 路径
WS_PATH = "/ws/notifications"

# C 网络内的 LLM API 地址
INTERNAL_LLM_BASE = os.environ.get("INTERNAL_LLM_BASE", "http://127.0.0.1:9000")

# TLS 证书路径
CERT_FILE = os.environ.get("CERT_FILE", "certs/server.crt")
KEY_FILE = os.environ.get("KEY_FILE", "certs/server.key")

# 是否使用 TLS（auto 时根据证书文件是否存在判断）
RELAY_TLS = os.environ.get("RELAY_TLS", "auto")

HEARTBEAT_MIN = 20
HEARTBEAT_MAX = 40

# 重连退避
RECONNECT_BASE = 5
RECONNECT_MAX = 60

# 请求超时（秒）
REQUEST_TIMEOUT = 120

# ── X coordinator integration ─────────────────────────────────────────────────
# All on-disk artifacts (cache, releases, logs, sqlite, systemd unit files) live
# under LLMROUTER_HOME. Tests redirect this to a tmp dir.

LLMROUTER_HOME = os.environ.get("LLMROUTER_HOME", os.path.expanduser("~/.llmrouter"))
CACHE_DIR = os.path.join(LLMROUTER_HOME, "cache")
RELEASES_DIR = os.path.join(LLMROUTER_HOME, "releases")
LOG_DIR = os.path.join(LLMROUTER_HOME, "logs")

# X coordinator
X_BASE_URL = os.environ.get("X_BASE_URL", "")  # required; set in .env — no hardcoded default
GROUP_ID = os.environ.get("GROUP_ID", "")
CLIENT_ID = os.environ.get("CLIENT_ID", "")
ROLE = os.environ.get("ROLE", "")  # B or C — set explicitly by relay/tunnel

X_HEARTBEAT_INTERVAL = int(os.environ.get("X_HEARTBEAT_INTERVAL", "30"))
X_AUDIT_BATCH_INTERVAL = int(os.environ.get("X_AUDIT_BATCH_INTERVAL", "5"))
X_AUDIT_BATCH_MAX = int(os.environ.get("X_AUDIT_BATCH_MAX", "50"))
X_AUDIT_QUEUE_MAX = int(os.environ.get("X_AUDIT_QUEUE_MAX", "1024"))
ELECTION_POLL_INTERVAL = int(os.environ.get("ELECTION_POLL_INTERVAL", "5"))
SELF_UPDATE_INTERVAL = int(os.environ.get("SELF_UPDATE_INTERVAL", "3600"))

# X server-only
X_DB_PATH = os.environ.get(
    "X_DB_PATH", os.path.join(LLMROUTER_HOME, "data", "x.sqlite")
)


def _ensure_dirs():
    """Create the LLMROUTER_HOME subtree, but never anything outside it."""
    for p in (CACHE_DIR, RELEASES_DIR, LOG_DIR, os.path.dirname(X_DB_PATH)):
        try:
            os.makedirs(p, exist_ok=True)
        except OSError:
            pass


def load_or_create_client_id() -> str:
    """Return a stable per-host client id for this role; persist to cache."""
    import json
    import uuid

    if CLIENT_ID:
        return CLIENT_ID
    _ensure_dirs()
    path = os.path.join(CACHE_DIR, "client_id.json")
    role_key = ROLE or "default"
    data = {}
    if os.path.exists(path):
        try:
            with open(path) as f:
                data = json.load(f)
        except Exception:
            data = {}
    cid = data.get(role_key)
    if not cid:
        cid = uuid.uuid4().hex
        data[role_key] = cid
        try:
            with open(path, "w") as f:
                json.dump(data, f)
        except Exception:
            pass
    return cid
