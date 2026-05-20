# llmrouter

将内网大模型 API 通过中间节点安全中继到外部，所有流量伪装为普通 HTTPS 网站访问。

## 架构

三方角色（用户只需部署 C）：

```
A (Claude Code 客户端)
        │
        │  HTTPS  /g/{group_id}/anthropic/v1/messages
        ▼
X (yinaisvr.duckdns.org)          ← 唯一需要公网 IP 的节点
        │  控制面：group 注册/心跳/选主/审计/版本分发
        │  数据面：WS 隧道多路复用 {group_id → ws}
        │
        │  WSS 出站  /ws/notifications
        ▼
C (内网隧道客户端)
        │
        ▼
    内网 LLM API
```

- **X**：固定部署在 `yinaisvr.duckdns.org`。同时承担控制面（sqlite 持久化 group/client/audit）和数据面（relay 多租户 WS 隧道）。
- **C**：用户部署在内网机器，主动出站连接 X。同一 group 可启动多个 C 待命，X 选主只有一个 active。
- **A**：Claude Code 客户端，配置 `ANTHROPIC_BASE_URL=https://yinaisvr.duckdns.org/g/{group_id}` 即可，无需安装任何 llmrouter 程序。

`group_id` 格式：`{phone(11位数字)}_{suffix(1-32 [A-Za-z0-9_-])}`。一个手机号可创建多个 group。

## 安装

所有文件仅落在 `~/.llmrouter/` 子树下。

### 第一步：创建 group

```bash
curl -X POST https://yinaisvr.duckdns.org/api/groups \
  -H 'Content-Type: application/json' \
  -d '{"phone":"13800138000","suffix":"home"}'
# → {"group_id":"13800138000_home","tunnel_secret":"tun-..."}
```

### 第二步：安装 C（内网机器）

```bash
curl -fsSL https://yinaisvr.duckdns.org/install/c.sh | \
  GROUP_ID=13800138000_home INTERNAL_LLM_BASE=http://10.0.0.5:8000 bash
```

完成后：
- 程序装到 `~/.llmrouter/c/`
- systemd unit：`~/.llmrouter/systemd/llmrouter-c.service`
- `systemctl --user enable --now llmrouter-c` 启动，`loginctl enable-linger $USER` 开机自启
- 同 group 多台机器都安装即可，X 自动选主，仅 1 个 active

### 第三步：配置 A（Linux/macOS/WSL）

```bash
curl -fsSL https://yinaisvr.duckdns.org/install/a.sh | \
  bash -s -- --group-id 13800138000_home
```

脚本生成 `~/.llmrouter/a/env.sh`，不自动修改 `~/.bashrc` 或 `~/.claude/settings.json`，会打印合并指令让你手动决定。

### 配置 A（Windows）

```powershell
iwr https://yinaisvr.duckdns.org/install/a.ps1 -UseBasicParsing | iex
```

未装 WSL 时自动引导安装 Ubuntu，随后在 WSL 内运行 `install/a.sh`。

## 查询 group 状态

```bash
curl https://yinaisvr.duckdns.org/api/groups?phone=13800138000
curl https://yinaisvr.duckdns.org/api/groups/13800138000_home
```

## 部署 X（yinaisvr.duckdns.org 机器）

```bash
git clone git@github.com:yinrong/llm-router.git
cd llm-router
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# 启动（默认 0.0.0.0:8000，数据在 ~/.llmrouter/）
python -m x

# 覆盖默认值：
X_HOST=0.0.0.0 X_PORT=443 X_BASE_URL=https://yinaisvr.duckdns.org python -m x
```

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `X_HOST` | `0.0.0.0` | 监听地址 |
| `X_PORT` | `8000` | 监听端口 |
| `X_BASE_URL` | `https://yinaisvr.duckdns.org` | 对外域名（写入安装脚本） |
| `LLMROUTER_HOME` | `~/.llmrouter` | 数据根目录 |
| `X_DB_PATH` | `~/.llmrouter/data/x.sqlite` | sqlite 路径 |

验证：

```bash
curl http://localhost:8000/healthz    # → {"ok":true,"version":"0.1.0"}
curl http://localhost:8000/           # → 伪装 HTML 页
```

**生产环境 TLS**：用 `setup_tls.sh` 申请 Let's Encrypt 证书后以 aiohttp 原生 SSL 启动，或在前面挂 nginx/caddy 做 TLS 终止。用 systemd 守护：

```ini
# ~/.llmrouter/systemd/llmrouter-x.service
[Service]
ExecStart=/path/to/venv/bin/python -m x
Restart=always
Environment=X_PORT=443
Environment=X_BASE_URL=https://yinaisvr.duckdns.org
```

## 开发

```bash
git clone git@github.com:yinrong/llm-router.git
cd llm-router
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt pytest pytest-asyncio

python -m pytest tests/ -v       # 45 项，~3.5 秒
python -m x                      # 本地启动 X（含 relay）
python _server.py                 # 本地启动 C（连 X）
```

## 文件说明

| 文件/目录 | 用途 |
|---|---|
| `x/` | X 服务包：aiohttp 路由、sqlite 层、选主、审计、版本、relay |
| `x/relay.py` | 多租户 relay（WS 隧道 + API 路由） |
| `x/scripts/*.tmpl` | curl 安装脚本与 systemd unit 模板 |
| `_server.py` | C：内网隧道客户端 |
| `c_x_client.py` | C 与 X 控制面通信（注册/心跳/选主） |
| `relay_server.py` | 单租户 relay（本地 dev 工具，生产用 `python -m x`） |
| `b_x_client.py` | 单租户 B 的 X 客户端（配合 relay_server.py 使用） |
| `c_replicate.py` | C 自扩散 stub（未实现，见 docs/replication.md） |
| `static/index.html` | 伪装网站首页 |
| `gen_cert.py` / `setup_tls.sh` | 自签名 / Let's Encrypt 证书 |
| `tests/` | 端到端测试（无 mock，plain HTTP） |

## 文件落地约束

llmrouter 在用户机器上**只写 `~/.llmrouter/` 子树**：

```
~/.llmrouter/
├── c/、x/、a/          # 各角色部署目录
├── cache/              # client_id.json、c-tunnel.json
├── data/x.sqlite       # 仅 X
├── releases/           # 自更新缓存
├── logs/
└── systemd/llmrouter-{c,x}.service
```

唯一例外：`systemctl --user link` 在 `~/.config/systemd/user/` 建 symlink（systemd 自身行为）。
