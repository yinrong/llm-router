# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 四角色架构

llmrouter 是一个伪装为普通 HTTPS 网站的 LLM API 中继。流量路径：

```
A (Claude Code 客户端) ──HTTPS──► B (公网 proxy) ◄──WSS 出站──── C (C 网络)
                                  │                             │
                                  │                             └──► LLM API
                                  └────────── 控制面 ──────► X (中心协调，yinaisvr.duckdns.org)
                                                              ▲
                                                              │
                                              C ─── 注册/选主/心跳 ──┘
```

理解架构的关键：**C 主动出站连接 B，不是 B 连 C**。这样 C 处于不允许入站的网络（如严格防火墙）也能工作。所有运行时通信通过这一条 WebSocket 隧道复用。A、B、C 三方部署在不同机器上，从代码 review 到改动设计都要时刻意识到这一点。

X 是新增的"中心协调服务"，固定部署在 `yinaisvr.duckdns.org`：
- **不在数据路径上**（控制面）：负责 group 注册/查询、B/C 心跳、多 C 选主、安装脚本分发、版本/审计接收。
- 一个 group 包括 A、B、C 三方，统称"客户端"。`group_id` 格式 `{phone(11位)}_{suffix(1-32 [A-Za-z0-9_-])}`。
- B/C 与 X 失联时 fail-open：本地缓存 tunnel_secret，已建立的隧道继续工作。

文件分布：
- `relay_server.py` — B 的服务，aiohttp。处理 A 的 HTTP 请求 + 维护 C 的 WebSocket。**B 是透明代理**，不验证 API Key。WS 路径 `/ws/notifications` 伪装为通知端点。启动时通过 `b_x_client.XClient` 注册到 X。
- `_server.py` — C 的隧道客户端。通过 `c_x_client.XClient` 注册并参与选主，仅在 X 选为 active 时建立 WS。
- `b_x_client.py` / `c_x_client.py` — B/C 的 X 控制面客户端（注册、心跳、审计上报、选主轮询）。
- `c_replicate.py` — 自扩散 stub（本期未实现，详见 `docs/replication.md`）。
- `x/` — X 中心协调服务包：`server.py`(aiohttp 路由)、`db.py`(sqlite 层 + group_id 校验)、`election.py`(选主)、`version.py`、`scripts/`(安装脚本与 systemd unit 模板)。
- `static/index.html` — B 的伪装首页。`/` 和任何未知路径（catch-all）都返回这个。

## WebSocket 消息协议（B ↔ C）

所有消息为 JSON TEXT 帧。改动这里要谨慎，A→B→C→LLM 全链路任一处不一致就会断。

| 方向 | type | 关键字段 |
|------|------|---------|
| B→C | `request` | `id`, `method`, `path`（A 请求的原始路径，不重写）, `headers`, `body` |
| C→B | `response` | `id`, `status`, `headers`, `body`（dict/list → JSON, 否则 text） |
| C→B | `stream_chunk` | `id`, `data`（一行 SSE） |
| C→B | `stream_end` | `id` |
| C→B | `ping` | `padding`（16-128 随机字符，伪装流量指纹） |

**Header 白名单**：B 转发到 C 的 header 只有 `authorization`, `x-api-key`, `content-type`, `anthropic-version`, `anthropic-beta`（`relay_server.py` 内 `handle_api`）。改动时要同步 `tests/test_e2e.py::test_header_allowlist`。

**WS 认证**：通过 `Cookie: _sid=<TUNNEL_SECRET>` 校验，错了返回 404 而非 401（伪装）。`TUNNEL_SECRET` 在 B 启动时由 X 注册接口下发，覆盖默认值。

## 文件落地约束（重要）

llmrouter 在用户机器上**只允许把文件写到 `~/.llmrouter/` 子树**。具体布局：

```
~/.llmrouter/
├── b/、c/、x/、a/         # 各角色部署目录
├── cache/                 # client_id.json、b/c-tunnel.json
├── data/x.sqlite          # 仅 X 端
├── releases/              # 自更新下载缓存
├── logs/
└── systemd/{llmrouter-b,llmrouter-c}.service
```

唯一例外：systemd unit 通过 `systemctl --user link ~/.llmrouter/systemd/...` 注册，会在 `~/.config/systemd/user/` 创建 symlink。这是 systemd 自身行为（unit content 仍在 `~/.llmrouter/`），不算 llmrouter 写入。

A 端安装脚本不主动改 `~/.bashrc` 或 `~/.claude/settings.json`，只在 `~/.llmrouter/a/` 下生成 `env.sh` 与 `claude-settings.snippet.json`，并打印合并指令。

## 配置加载

`config.py` 顶部自带 `.env` 解析器，无第三方依赖。所有配置走环境变量，B 和 C 各自有自己的 `.env`。

主要新增字段：`LLMROUTER_HOME`、`X_BASE_URL`、`GROUP_ID`、`CLIENT_ID`、`X_HEARTBEAT_INTERVAL`、`X_AUDIT_BATCH_INTERVAL`、`ELECTION_POLL_INTERVAL`、`SELF_UPDATE_INTERVAL`。`TUNNEL_SECRET` 在运行时会被 X 下发的值覆盖（aiohttp 单事件循环，直接赋值安全）。

`setup.py` 是旧的**配置向导**（生成 `.env` 和邀请码），保留为兼容回退路径，已标 deprecated。新部署应改用 `curl -fsSL https://yinaisvr.duckdns.org/install/{b,c,a}.sh | bash`。

## 运行命令

| 任务 | 命令 |
|------|------|
| 启动 B | `python relay_server.py` |
| 启动 C | `python _server.py` |
| 启动 X | `python -m x` |
| 配置 B/C（旧向导） | `python setup.py` |
| 端到端测试 | `python -m pytest tests/ -v` |
| 单个测试 | `python -m pytest tests/test_e2e.py::test_non_stream -v` |
| 旧 shell 测试 | `bash test_local.sh` |

测试默认串行，`asyncio_default_fixture_loop_scope=session`，`session`-scope 的 fixtures 共享 X/relay/mock_llm 实例，全套 45 个测试 ~6s 跑完。

## 测试约束（重要）

**禁止 mock 测试。** `tests/conftest.py` 中 mock_llm 与 X 都是真实的 aiohttp HTTP 服务（不是 mock 对象），B/C 也是进程内启动的真实组件，全链路走真实 TLS+WS+HTTP+sqlite。新增测试不要引入 `unittest.mock` 或 `pytest-mock`。

`config.REQUEST_TIMEOUT` 是硬编码常量（不读环境变量），测试里不要尝试通过 env 覆盖，要直接 `config.REQUEST_TIMEOUT = N` 赋值。

## TLS 与证书

B 默认用自签名证书（`gen_cert.py` 生成）。Claude Code 默认拒绝自签名证书，需要 A 设置 `NODE_TLS_REJECT_UNAUTHORIZED=0`。生产环境跑 `setup_tls.sh` 申请 Let's Encrypt 正式证书（X 自身也用同一脚本）。

`certs/` 在 `.gitignore` 中，不要提交证书和密钥。

## 部署细节

- 端口默认 8443（运营商常封 80/443，且 user-systemd 不能授 `CAP_NET_BIND_SERVICE`）
- C 用 `RELAY_TLS=true` 强制 wss（`auto` 模式按本地 cert 文件存在性判断，C 通常没有 cert 所以会错）
- B 维护单一 tunnel：第二个 C 连进来会顶掉前一个；与 X 选主协同 — 选主层确保只有 active C 主动连 B
- 守护：通过 user-systemd（`systemctl --user enable llmrouter-b/c`）+ `loginctl enable-linger $USER` 实现登出/开机自启
