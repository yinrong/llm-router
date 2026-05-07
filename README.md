# llmrouter

通过中间节点，将内网的大模型 API 安全中继到外部，所有流量伪装为正常 HTTPS 网站访问；多节点统一由中心协调服务管理。

## 架构

四方角色：

```
A (Claude Code 客户端)      B (公网 relay)              C (内网隧道)
     │                          │                           │
     │ HTTPS 请求               │     WSS 出站连接          │
     │ /anthropic/v1/messages ► │ ◄── /ws/notifications     │
     │                          │                           │
     │ ◄── JSON 响应 ────────── │ ──► 内网 LLM API ─────►   │
                                │
                                ▼  控制面（注册/选主/审计/版本）
                       X (yinaisvr.duckdns.org)
                       └── sqlite ── group/client/audit
```

- **A**：Claude Code 客户端，任意网络。
- **B**：每个 group 一个 B（用户自有公网 IP）；对外是普通 HTTPS 网站，内里把请求经 WSS 转发给 C。
- **C**：内网隧道客户端，主动出站连接 B。同 group 可启动多个 C 待命，X 选主只有一个 active。
- **X**：中心协调服务（固定域名 `yinaisvr.duckdns.org`），sqlite 持久化所有 group。控制面，**不在数据路径上**。

一个 group 的 `group_id` 格式：`{phone(11位数字)}_{suffix(1-32 [A-Za-z0-9_-])}`。一个手机号可创建多个 group。

## 安装（curl 一键）

A、B、C 三方都通过 `curl ... | bash` 安装；所有文件落地仅在 `~/.llmrouter/` 子树下。

### B 端（公网机器）

```bash
curl -fsSL https://yinaisvr.duckdns.org/install/b.sh | GROUP_ID=13800138000_home bash
```

完成后：
- 装到 `~/.llmrouter/b/`，systemd unit 在 `~/.llmrouter/systemd/llmrouter-b.service`
- 通过 `systemctl --user enable --now llmrouter-b` 启动；`loginctl enable-linger $USER` 开机自启
- 默认监听 8443（user-systemd 不能授 `CAP_NET_BIND_SERVICE` 监听 443）

### C 端（内网机器）

```bash
curl -fsSL https://yinaisvr.duckdns.org/install/c.sh | \
  GROUP_ID=13800138000_home INTERNAL_LLM_BASE=http://10.0.0.5:8000 bash
```

同 group 的多个 C 都装上即可，X 自动选主，仅 1 个 active。

### A 端（Linux/macOS/WSL）

```bash
curl -fsSL https://yinaisvr.duckdns.org/install/a.sh | bash -s -- --group-id 13800138000_home
```

脚本写 `~/.llmrouter/a/env.sh` 与 `claude-settings.snippet.json`，**不主动**改 `~/.bashrc` 或 `~/.claude/settings.json`，会打印合并指令让你手动选择。

### A 端（Windows）

```powershell
iwr https://yinaisvr.duckdns.org/install/a.ps1 -UseBasicParsing | iex
```

脚本检测 WSL；未装则 `wsl --install -d Ubuntu`（Win10 需重启），随后在 WSL 内运行 `install/a.sh`。Windows 主机上不落地任何 llmrouter 文件。

## 查询 group 状态

```bash
curl https://yinaisvr.duckdns.org/api/groups?phone=13800138000
curl https://yinaisvr.duckdns.org/api/groups/13800138000_home
```

## 自更新

B/C 周期性轮询 `GET /api/version/{role}`，发现新版本时下载 tarball（带 sha256 校验）到 `~/.llmrouter/releases/`，由 systemd `Restart=always` 切换。默认 1 小时 + 0–600 秒抖动。

## 开发

```bash
git clone git@github.com:yinrong/llm-router.git
cd llm-router
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
pip install pytest pytest-asyncio
python -m pytest tests/ -v          # 全套 ~6 秒
```

各组件本地启动：
- X：`python -m x`
- B：`python relay_server.py`
- C：`python _server.py`

## 文件说明

| 文件 | 用途 |
|------|------|
| `relay_server.py` | B：公网 relay（aiohttp） |
| `_server.py` | C：内网隧道客户端 |
| `b_x_client.py` / `c_x_client.py` | B/C 与 X 控制面通信 |
| `c_replicate.py` | C 自扩散 stub（未实现） |
| `x/` | X 中心协调服务包（aiohttp + sqlite） |
| `x/scripts/*.tmpl` | 安装脚本与 systemd unit 模板 |
| `static/index.html` | 伪装网站首页 |
| `gen_cert.py` / `setup_tls.sh` | 自签名 / Let's Encrypt 证书 |
| `setup.py` | 旧版交互向导（已 deprecated，保留兼容） |
| `tests/` | 端到端测试（无 mock） |
| `docs/replication.md` | C 自扩散设计草案 |

## 文件落地约束

llmrouter 在用户机器上**只允许把文件写到 `~/.llmrouter/` 子树**：

```
~/.llmrouter/
├── b/、c/、x/、a/          # 各角色部署目录
├── cache/                  # client_id.json、b/c-tunnel.json
├── data/x.sqlite           # 仅 X
├── releases/               # 自更新缓存
├── logs/
└── systemd/{llmrouter-b,llmrouter-c}.service
```

唯一例外：`systemctl --user link` 会在 `~/.config/systemd/user/` 建一个 symlink（systemd 自身行为），unit 内容仍在 `~/.llmrouter/`。
