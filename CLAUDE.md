# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 架构

llmrouter 是一个 LLM API 中继，通过隧道穿透让内网 LLM 服务安全地对外提供 API。流量路径：

```
A (Claude Code 客户端) ──HTTP──► X (公网中继) ◄──WSS 出站──── C (内网)
                                 │                           │
                                 │ /g/{group_id}/*           └──► LLM API
                                 │
                                 └──── 控制面 ────────────────────────────
                                       /api/* 注册/选主/心跳/版本/审计
```

理解架构的关键：**C 主动出站连接 X，不是 X 连 C**。这样 C 处于只能出站的网络（如严格防火墙）也能工作。所有运行时通信通过这一条 WebSocket 隧道复用。A、X、C 三方部署在不同机器上。

X 部署在有公网 IP 的服务器，域名通过 `X_BASE_URL` 环境变量配置，无硬编码默认值。

- `x/server.py` — X 的入口，aiohttp。同时处理控制面（`/api/*`）和数据面（`/g/{group_id}/*`、`/ws/notifications`）。
- `x/relay.py` — 多租户中继逻辑：维护各 group 的 WS 隧道，将 A 的请求路由到对应 group 的活跃 C。
- `_server.py` — C 的隧道客户端。通过 `c_x_client.XClient` 注册并参与选主，仅在 X 选为 active 时建立 WS。
- `b_x_client.py` / `c_x_client.py` — X 控制面客户端（注册、心跳、审计上报、选主轮询）。
- `c_replicate.py` — C 自扩散 stub（本期未实现，见 `docs/replication.md`）。
- `x/db.py` — sqlite 层 + group_id 格式校验。
- `x/election.py` — 多 C 选主逻辑。
- `x/scripts/` — 安装脚本与 systemd unit 模板。
- `static/index.html` — X 根路径返回的静态页面（`/` 和任何未注册路径均返回此页）。

## WS 消息协议（X ↔ C）

所有消息为 JSON TEXT 帧。改动这里要谨慎，全链路任一处不一致就会断。

| 方向 | type | 关键字段 |
|------|------|---------|
| X→C | `request` | `id`, `method`, `path`, `headers`, `body` |
| C→X | `response` | `id`, `status`, `headers`, `body`（dict/list → JSON, 否则 text） |
| C→X | `stream_chunk` | `id`, `data`（一行 SSE） |
| C→X | `stream_end` | `id` |
| C→X | `ping` | `padding`（16-128 随机字符，流量填充） |

**Header 白名单**（`x/relay.py:_ALLOWED_HEADERS`）：X 转发到 C 的 header 只有 `authorization`, `x-api-key`, `content-type`, `anthropic-version`, `anthropic-beta`。改动时同步 `tests/test_e2e.py::test_header_allowlist`。

**WS 认证**：通过 `Cookie: _sid=<TUNNEL_SECRET>` 校验，错误时返回 404（不暴露端点是否存在）。`TUNNEL_SECRET` 由 X 在 group 创建时生成并持久化在 sqlite，C 注册时由 `/api/register/c` 下发。

## 文件落地约束（重要）

llmrouter 在用户机器上**只允许把文件写到 `~/.llmrouter/` 子树**。具体布局：

```
~/.llmrouter/
├── c/                     # C 部署目录
├── cache/                 # client_id.json、c-tunnel.json
├── data/x.sqlite          # 仅 X 端
├── releases/              # 自更新下载缓存
├── logs/
└── systemd/llmrouter-c.service
```

唯一例外：systemd unit 通过 `systemctl --user link ~/.llmrouter/systemd/...` 注册，会在 `~/.config/systemd/user/` 创建 symlink（systemd 自身行为，unit 内容仍在 `~/.llmrouter/`）。

A 端安装脚本不修改 `~/.bashrc` 或 `~/.claude/settings.json`，只在 `~/.llmrouter/a/` 生成配置片段并打印合并指令。

## 配置加载

`config.py` 顶部自带 `.env` 解析器，无第三方依赖。

关键字段：

| 变量 | 用途 |
|------|------|
| `X_BASE_URL` | X 的公网地址，必须设置，无默认值（示例：`https://your-server.example.com`） |
| `GROUP_ID` | 本机所属 group |
| `CLIENT_ID` | 本进程 ID（空则自动生成并持久化） |
| `INTERNAL_LLM_BASE` | C 端：内网 LLM 地址 |
| `LLMROUTER_HOME` | 数据根目录（默认 `~/.llmrouter`） |
| `X_HEARTBEAT_INTERVAL` | 向 X 汇报心跳间隔（秒，默认 30） |
| `ELECTION_POLL_INTERVAL` | 选主轮询间隔（秒，默认 5） |
| `SELF_UPDATE_INTERVAL` | 自更新检查间隔（秒，默认 3600） |
| `REQUEST_TIMEOUT` | 请求超时（秒，**硬编码常量，不读 env**，测试直接赋值 `config.REQUEST_TIMEOUT = N`） |

`setup.py` 是旧版交互向导，保留为回退路径，已标 deprecated。新部署用 curl 安装脚本。

## 运行命令

| 任务 | 命令 |
|------|------|
| 启动 X | `python -m x` |
| 启动 C | `python _server.py` |
| 配置（旧向导） | `python setup.py` |
| 端到端测试 | `python -m pytest tests/ -v` |
| 单个测试 | `python -m pytest tests/test_e2e.py::test_non_stream -v` |

测试默认串行，`asyncio_default_fixture_loop_scope=session`，全套 ~50 个测试 ~6s 跑完。

## 测试约束（重要）

**禁止 mock 测试。** `tests/conftest.py` 中 mock_llm 与 X 都是真实的 aiohttp HTTP 服务（不是 mock 对象），C 也是进程内启动的真实组件，全链路走真实 WS+HTTP+sqlite。新增测试不要引入 `unittest.mock` 或 `pytest-mock`。

`config.REQUEST_TIMEOUT` 是硬编码常量（不读环境变量），测试里不要尝试通过 env 覆盖，要直接 `config.REQUEST_TIMEOUT = N` 赋值。

## 部署细节

- `X_BASE_URL` 必须在 `.env` 中显式设置（如 `X_BASE_URL=https://your-server.example.com:8443`）
- C 用 `X_BASE_URL` 的 scheme 决定 WS 方案：`https://` → `wss://`，`http://` → `ws://`（测试用 http）
- X 维护多 group 的隧道；同 group 有多个 C 时，X 选主确保只有一个 active C 建立 WS
- 守护：通过 user-systemd（`systemctl --user enable llmrouter-c`）+ `loginctl enable-linger $USER` 实现开机自启
