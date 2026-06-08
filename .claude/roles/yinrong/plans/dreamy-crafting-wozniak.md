# DDD+TDD 重构计划

## Context

当前代码库缺乏分层：x/server.py handler 直接操作 sqlite3 Connection，MultiTenantRelay 混合了 tunnel 状态管理、审计写入和静态页面，C 端通过 `config.TUNNEL_SECRET = secret` 变异全局变量，没有 Domain 对象也没有单元测试。

重构目标：引入 DDD 分层（Domain / Application / Infrastructure / Presentation），用 InMemory Repository 实现单元测试隔离，删除已废弃的旧 B 文件，全程保持现有集成测试绿色。

## 目标目录结构

```
llm-router/
├── config.py                     # 保留，仅存常量；移除对外暴露的变异点
├── c/                            # C 端新包（替代 _server.py, c_x_client.py）
│   ├── __init__.py
│   ├── __main__.py               # python -m c 入口
│   ├── settings.py               # CSettings 不可变值对象
│   ├── tunnel_worker.py          # 原 Worker → TunnelWorker（依赖注入）
│   ├── x_client.py               # 原 c_x_client.XClient（依赖注入 settings）
│   └── cache.py                  # tunnel_secret 缓存读写
│
├── x/
│   ├── domain/                   # 纯 Python，零外部依赖
│   │   ├── group.py              # GroupId 值对象, Group dataclass
│   │   ├── client.py             # ClientRole 枚举, Client dataclass
│   │   ├── audit.py              # AuditEvent dataclass
│   │   └── election.py           # decide_election() 纯函数, ElectionDecision
│   │
│   ├── application/              # 业务流程编排，只接受 Repository ABC
│   │   ├── group_service.py
│   │   ├── registration_service.py
│   │   ├── heartbeat_service.py
│   │   ├── election_service.py   # 调用 domain.election.decide_election
│   │   └── audit_service.py
│   │
│   ├── infrastructure/
│   │   ├── repositories/
│   │   │   ├── base.py           # GroupRepository / ClientRepository / AuditRepository ABC
│   │   │   ├── sqlite_group_repo.py
│   │   │   ├── sqlite_client_repo.py   # 含 force_active_for_test()
│   │   │   ├── sqlite_audit_repo.py
│   │   │   └── in_memory/        # 单元测试用实现，无 mock 库
│   │   │       ├── group_repo.py
│   │   │       ├── client_repo.py
│   │   │       └── audit_repo.py
│   │   └── db.py                 # 保留 connect() + init_schema()
│   │
│   ├── presentation/
│   │   ├── handlers.py           # 所有 aiohttp handler，从 request.app["services"] 取 service
│   │   └── app_factory.py        # create_app()：组装 repos → services → relay → router
│   │
│   ├── relay/
│   │   ├── multi_tenant.py       # MultiTenantRelay，构造函数注入 AuditService
│   │   └── protocol.py           # WS 消息类型常量 + 构造/解析辅助
│   │
│   ├── server.py                 # 保留但委托给 presentation/app_factory.py
│   ├── version.py                # 不变
│   ├── scripts/                  # 不变
│   ├── __init__.py               # VERSION
│   └── __main__.py               # 不变
│
└── tests/
    ├── conftest.py               # Step6 修改 tunnel fixture，移除 importlib.reload
    ├── test_e2e.py               # 保留（验收标准）
    ├── test_x.py                 # 保留
    ├── test_register.py          # 保留
    ├── test_election.py          # 保留
    ├── test_audit.py             # 保留
    └── unit/                     # 新增单元测试，毫秒级，无网络
        ├── test_domain_group.py
        ├── test_domain_client.py
        ├── test_domain_audit.py
        ├── test_domain_election.py
        ├── test_app_registration.py
        ├── test_app_election.py
        ├── test_app_audit.py
        └── test_c_settings.py
```

**删除文件**：`relay_server.py`、`b_x_client.py`、`_server.py`、`c_x_client.py`

## 关键 Domain 对象

### x/domain/group.py
```python
@dataclass(frozen=True)
class GroupId:
    value: str
    # __post_init__ 校验 ^(\d{11})_([A-Za-z0-9_-]{1,32})$
    # .phone, .suffix property
    # classmethod of(phone, suffix)

@dataclass(frozen=True)
class Group:
    group_id: GroupId; phone: str; suffix: str
    tunnel_secret: str; created_at: int
    b_addr: str|None; b_port: int|None; b_last_seen: int|None
```

### x/domain/election.py
```python
@dataclass(frozen=True)
class ElectionDecision:
    winner_id: str; winner_since: int; took_over: bool

def decide_election(requester_id, candidates, *, now_ts, stale_threshold) -> ElectionDecision:
    # 纯函数：无 active → 按 registered_at/client_id 选最早
    # active stale → requester 接管
    # active 有效 → 保持不变
```

### c/settings.py
```python
@dataclass(frozen=True)
class CSettings:
    x_base_url: str; group_id: str; client_id: str; tunnel_secret: str
    internal_llm_base: str; cache_dir: str; ...
    @property def ws_url(self) -> str  # https→wss, http→ws
    def with_tunnel_secret(self, s) -> "CSettings"  # 返回新实例，不变异全局
    @classmethod def from_env(cls) -> "CSettings"
```

## Repository 接口（x/infrastructure/repositories/base.py）

```python
class GroupRepository(ABC):
    save(group) / get(group_id) / get_by_secret(secret)
    list_by_phone(phone) / update_b_addr(...)

class ClientRepository(ABC):
    upsert(client) / get(client_id) / list_by_group(group_id, role)
    update_heartbeat(client_id, ts) -> bool
    set_active(group_id, winner_id, ts) / clear_active(group_id)
    get_candidates(group_id) -> List[CandidateSnapshot]

class AuditRepository(ABC):
    insert_many(events) -> int
```

InMemory 实现用真实语义（如 save 时重复抛 ValueError），不用 mock 库。

## Application Services

| Service | 方法 | 调用 |
|---------|------|------|
| GroupService | create_group, get_group, list_groups | GroupRepository |
| RegistrationService | register_b, register_c | GroupRepo + ClientRepo |
| HeartbeatService | heartbeat | ClientRepository |
| ElectionService | claim_active | ClientRepo + domain.decide_election |
| AuditService | post_events, record_relay_event | AuditRepo + GroupRepo |

## 迁移步骤

每步结束后 `python -m pytest tests/ -v` 必须全绿。

**Step 1 — Domain 层（纯新增）**
- 创建 `x/domain/` 下四个文件
- 创建 `tests/unit/test_domain_*.py`（4个）
- 现有测试：不受影响

**Step 2 — Repository ABC + InMemory 实现（纯新增）**
- 创建 `x/infrastructure/repositories/base.py` 和 `in_memory/` 下三个实现
- 创建 `tests/unit/test_app_*.py`（3个）只用 InMemory
- 现有测试：不受影响

**Step 3 — Application Services（纯新增）**
- 创建 `x/application/` 下五个 Service
- Service 构造函数只接受 Repository ABC
- 现有测试：不受影响

**Step 4 — Sqlite Repository 实现**
- 创建 `x/infrastructure/repositories/sqlite_*.py`，SQL 从 `x/db.py` 迁入
- `x/db.py` 旧函数**暂不删除**（共存）
- 现有测试：不受影响

**Step 5 — Presentation 层切换（最关键）**
- 创建 `x/presentation/handlers.py`：从 `request.app["services"]` 取 service
- 创建 `x/presentation/app_factory.py`：组装依赖树
- 修改 `x/relay.py` 的 `MultiTenantRelay.__init__`：注入 `AuditService`，移除直接 `db` 依赖
- 修改 `x/server.py`：`create_app()` 委托给 `app_factory.create_app()`
- **验收重点**：`test_audit.py`、`test_e2e.py` 全绿

**Step 6 — C 端重构**
- 创建 `c/` 包全部文件
- 修改 `tests/conftest.py` 的 `tunnel` fixture：改用 `TunnelWorker(settings)` 直接构造，移除 `importlib.reload`
- 修改 `tests/test_e2e.py` 中引用 `_server` 的两处导入（`test_tunnel_disconnect_reconnect`、`test_upstream_unreachable`）

**Step 7 — 删除废弃文件**
- 删除 `relay_server.py`, `b_x_client.py`, `_server.py`, `c_x_client.py`
- 删除 `x/db.py` 中已迁移的旧函数（保留 `connect`, `init_schema`, `now_ts`）
- 删除 `x/election.py`（逻辑已迁入 `domain/election.py` + `ElectionService`）
- 验证：`grep -r "from relay_server\|from b_x_client\|from _server import\|from c_x_client" . --include="*.py"` 无输出

**Step 8 — 更新 CLAUDE.md**
- 更新「文件分布」表：加 `c/` 包说明
- 更新「运行命令」：`python -m c` 启动 C

## 验证方式

```bash
# 单元测试（无网络，毫秒级）
python -m pytest tests/unit/ -v

# 集成 + E2E（原有，验收标准）
python -m pytest tests/ -v

# 无废弃引用
grep -r "from relay_server\|from b_x_client\|import _server\|from c_x_client" . --include="*.py"

# 确认 config 全局变异消除
grep -r "config\.TUNNEL_SECRET\s*=" . --include="*.py"
```
