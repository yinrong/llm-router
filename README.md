# llmrouter

将内网大模型 API 通过中间节点安全中继到外部，外观上是普通 HTTPS 服务。

## 架构

三方角色（用户只需部署 C）：

```
A (Claude Code 客户端)
        │
        │  HTTPS  /g/{group_id}/anthropic/v1/messages
        ▼
X (你的公网服务器)         ← 唯一需要公网 IP 的节点
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

- **X**：部署在你的公网服务器，地址通过 `X_BASE_URL` 环境变量配置。同时承担控制面（sqlite 持久化 group/client/audit）和数据面（relay 多租户 WS 隧道）。
- **C**：部署在内网机器，主动出站连接 X（适合只能出站的网络）。同一 group 可启动多个 C 待命，X 选主只有一个 active。
- **A**：Claude Code 客户端，配置 `ANTHROPIC_BASE_URL=https://<X地址>/g/{group_id}` 即可，无需安装 llmrouter 程序。

`group_id` 格式：`{phone(11位数字)}_{suffix(1-32 [A-Za-z0-9_-])}`。一个手机号可创建多个 group。

## 安装

所有文件仅落在 `~/.llmrouter/` 子树下。将下面命令中的 `X_BASE_URL` 替换为你实际的 X 服务器地址。

### 第一步：部署 X（你的公网服务器）

```bash
git clone https://github.com/yinrong/llm-router.git
cd llm-router
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
```

创建 `.env`：
```bash
cat > .env <<EOF
X_BASE_URL=https://your-server.example.com:8443
X_HOST=0.0.0.0
X_PORT=8443
LLMROUTER_HOME=$HOME/.llmrouter
EOF
```

用 systemd 自启动：
```bash
mkdir -p ~/.llmrouter/systemd

cat > ~/.llmrouter/systemd/llmrouter-x.service <<EOF
[Unit]
Description=llmrouter X
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$(pwd)
ExecStart=$(pwd)/venv/bin/python -m x
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=$(pwd)/.env

[Install]
WantedBy=default.target
EOF

loginctl enable-linger $USER
systemctl --user link ~/.llmrouter/systemd/llmrouter-x.service
systemctl --user daemon-reload
systemctl --user enable --now llmrouter-x
```

TLS 证书（推荐 Let's Encrypt）：
```bash
bash setup_tls.sh   # 引导填写域名，自动申请 Let's Encrypt 证书
```

验证：
```bash
curl http://localhost:8443/healthz    # → {"ok":true,"version":"0.1.0"}
```

### 第二步：创建 group

```bash
curl -X POST https://your-server.example.com:8443/api/groups \
  -H 'Content-Type: application/json' \
  -d '{"phone":"13800138000","suffix":"home"}'
# → {"group_id":"13800138000_home","tunnel_secret":"tun-..."}
```

### 第三步：安装 C（内网机器，全自动）

```bash
curl -fsSL https://your-server.example.com:8443/install/c.sh | \
  X_BASE_URL=https://your-server.example.com:8443 \
  GROUP_ID=13800138000_home \
  INTERNAL_LLM_BASE=http://10.0.0.5:8000 \
  bash
```

安装脚本全自动完成：
1. 下载代码到 `~/.llmrouter/c/`，创建 Python venv，安装依赖
2. 写入 `~/.llmrouter/c/.env`
3. 创建 `~/.llmrouter/systemd/llmrouter-c.service`
4. `loginctl enable-linger $USER` — 用户退出后服务仍运行
5. `systemctl --user enable --now llmrouter-c` — 立即启动并设为开机自启

无需任何手动操作。同 group 多台机器都安装即可，X 自动选主，仅 1 个 active。

### 第四步：配置 A（Linux/macOS/WSL）

```bash
curl -fsSL https://your-server.example.com:8443/install/a.sh | \
  X_BASE_URL=https://your-server.example.com:8443 \
  bash -s -- --group-id 13800138000_home
```

脚本在 `~/.llmrouter/a/` 生成配置片段，不自动修改 `~/.bashrc` 或 `~/.claude/settings.json`，会打印合并指令让你手动执行。

### 配置 A（Windows）

```powershell
iwr https://your-server.example.com:8443/install/a.ps1 -UseBasicParsing | iex
```

未装 WSL 时自动引导安装 Ubuntu，随后在 WSL 内运行 `install/a.sh`。

### 手动配置 A

将以下内容加入 `~/.claude/settings.json`（将地址和 Key 替换为实际值）：
```json
{
  "env": {
    "ANTHROPIC_BASE_URL": "https://your-server.example.com:8443/g/13800138000_home/anthropic",
    "ANTHROPIC_API_KEY": "<LLM 服务提供方的 API Key>",
    "NODE_TLS_REJECT_UNAUTHORIZED": "0"
  }
}
```

## 查询 group 状态

```bash
# 查询一个手机号下的所有 group
curl https://your-server.example.com:8443/api/groups?phone=13800138000

# 查询单个 group 详情（含 C 客户端列表和在线状态）
curl https://your-server.example.com:8443/api/groups/13800138000_home
```

## 自更新

C 定期向 X 查询 `/api/version/c`，发现新版本后自动下载（至 `~/.llmrouter/releases/`）并由 systemd 重启切换。默认 1 小时检查一次（带随机抖动，避免集中访问）。

## 开发

```bash
git clone https://github.com/yinrong/llm-router.git
cd llm-router
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt pytest pytest-asyncio

python -m pytest tests/ -v       # 全套测试，~4 秒
python -m x                      # 本地启动 X
python _server.py                # 本地启动 C
```

## X 服务器关键环境变量

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `X_BASE_URL` | （必填，无默认值）| X 的公网地址，写入安装脚本供 A/C 使用 |
| `X_HOST` | `0.0.0.0` | 监听地址 |
| `X_PORT` | `8000` | 监听端口 |
| `LLMROUTER_HOME` | `~/.llmrouter` | 数据根目录 |
| `X_DB_PATH` | `~/.llmrouter/data/x.sqlite` | sqlite 路径 |

## 文件说明

| 文件/目录 | 用途 |
|---|---|
| `x/` | X 服务包：HTTP 路由、sqlite 层、选主、审计、版本、relay |
| `x/relay.py` | 多租户 relay（WS 隧道 + API 路由） |
| `x/scripts/*.tmpl` | curl 安装脚本与 systemd unit 模板 |
| `_server.py` | C：内网隧道客户端 |
| `c_x_client.py` | C 与 X 控制面通信（注册/心跳/选主） |
| `c_replicate.py` | C 自扩散 stub（未实现，见 docs/replication.md） |
| `static/index.html` | X 根路径静态页 |
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

唯一例外：`systemctl --user link` 在 `~/.config/systemd/user/` 建软链接（systemd 自身行为，unit 内容仍在 `~/.llmrouter/`）。
