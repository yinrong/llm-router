#!/usr/bin/env python3
"""
全自动配置向导。
在 A/B/C 任意一台机器上运行，交互式完成配置。
"""

import base64
import json
import os
import secrets
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def _write_env(kvs: dict):
    """写 .env 到脚本所在目录。"""
    path = os.path.join(SCRIPT_DIR, ".env")
    with open(path, "w") as f:
        for k, v in kvs.items():
            f.write(f"{k}={v}\n")
    print(f"\n配置已写入: {path}")


def _encode_invite(data: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).decode()


def _decode_invite(code: str) -> dict:
    try:
        return json.loads(base64.urlsafe_b64decode(code.encode()))
    except Exception:
        print("错误: 邀请码无效，请检查是否完整复制。")
        sys.exit(1)


def setup_b():
    print("\n=== 配置 B（中继服务器） ===\n")

    ip = input("B 的公网 IP 地址: ").strip()
    if not ip:
        print("错误: IP 不能为空")
        sys.exit(1)

    port_str = input("监听端口 [443]: ").strip()
    port = int(port_str) if port_str else 443

    tunnel_secret = "tun-" + secrets.token_urlsafe(32)

    # 生成 TLS 证书
    print("\n正在生成 TLS 证书...")
    from gen_cert import generate_cert
    cert_dir = os.path.join(SCRIPT_DIR, "certs")
    generate_cert(cert_dir=cert_dir, ip_addr=ip)

    # 写 .env
    _write_env({
        "RELAY_HOST": "0.0.0.0",
        "RELAY_PORT": str(port),
        "RELAY_ADDR": ip,
        "TUNNEL_SECRET": tunnel_secret,
        "CERT_FILE": "certs/server.crt",
        "KEY_FILE": "certs/server.key",
    })

    # 生成 C 的邀请码
    invite_c = _encode_invite({"role": "C", "addr": ip, "port": port, "tunnel_secret": tunnel_secret})

    print("\n" + "=" * 60)
    print("配置完成！")
    print("=" * 60)
    print(f"\n【给 C 的邀请码】:")
    print(invite_c)
    print(f"\n【给 A 的连接信息】:")
    print(f"  ANTHROPIC_BASE_URL: https://{ip}:{port}/anthropic")
    print(f"\n启动命令: python relay_server.py")
    if port < 1024:
        print(f"注意: 端口 {port} 需要 root 权限，用 sudo python relay_server.py")


def setup_c():
    print("\n=== 配置 C（隧道客户端） ===\n")

    code = input("粘贴邀请码（从 B 获取的 C 码）: ").strip()
    data = _decode_invite(code)

    if data.get("role") != "C":
        print("错误: 这不是给 C 的邀请码，请使用 B 生成的 C 码。")
        sys.exit(1)

    llm_url = input("C 网络内的 LLM API 地址 (如 https://10.0.1.50:8080): ").strip()
    if not llm_url:
        print("错误: LLM API 地址不能为空")
        sys.exit(1)

    _write_env({
        "RELAY_ADDR": data["addr"],
        "RELAY_PORT": str(data["port"]),
        "TUNNEL_SECRET": data["tunnel_secret"],
        "INTERNAL_LLM_BASE": llm_url,
        "RELAY_TLS": "true",
    })

    print(f"\n启动命令: python _server.py")


def main():
    print("=" * 40)
    print("   llmrouter 配置向导（旧版，已弃用）")
    print("   推荐改用 X 协调服务的 curl 安装脚本：")
    print("   curl -fsSL https://yinaisvr.duckdns.org/install/b.sh | bash")
    print("=" * 40)
    print("\n选择本机角色:")
    print("  [B] claude-code-proxy（有公网 IP 的机器，第一个配置）")
    print("  [C] 隧道客户端（C 网络内的机器）")
    print()

    role = input("输入角色 (B/C): ").strip().upper()

    if role == "B":
        setup_b()
    elif role == "C":
        setup_c()
    else:
        print("错误: 请输入 B 或 C")
        sys.exit(1)


if __name__ == "__main__":
    main()
