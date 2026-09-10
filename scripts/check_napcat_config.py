# -*- coding: utf-8 -*-
"""检查 NapCat 配置是否正确指向 v2。

用途:
  在真号验收前运行,确认"发送通道"(httpServer)与"接收通道"(httpClient)
  都指向 v2,避免"消息发不出去/收不到"的低级问题。

用法:
  uv run python scripts/check_napcat_config.py
  uv run python scripts/check_napcat_config.py --config D:\\napcat\\config\\onebot11_XXXX.json
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# v2 的默认地址(与 .env 的 MEMO_ECHO_API_PORT / MEMO_ECHO_NAPCAT_BASE_URL 对应)
DEFAULT_V2_WEBHOOK = "http://127.0.0.1:8000/qq/webhook"
DEFAULT_V2_SEND_PORT = 3011


def main() -> int:
    parser = argparse.ArgumentParser(description="检查 NapCat 配置是否指向 v2")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(r"D:\napcat\config\onebot11_3969785168.json"),
        help="NapCat OneBot 配置文件路径",
    )
    args = parser.parse_args()

    if not args.config.exists():
        print(f"✗ 配置文件不存在: {args.config}")
        return 1

    with io.open(args.config, encoding="utf-8") as f:
        cfg = json.load(f)
    print(f"配置文件: {args.config}")
    print("JSON 解析: ✓\n")

    network = cfg.get("network") or {}
    ok = True

    # ---- 发送通道: v2 通过 httpServer 发消息 ----
    print("【发送通道】v2 调用的 HTTP 服务(应启用,端口与 .env 的 NAPCAT_BASE_URL 一致)")
    send_ok = False
    for server in network.get("httpServers") or []:
        mark = "启用" if server.get("enable") else "停用"
        print(f"  - {server.get('name')}: {mark}, 端口 {server.get('port')}, token={'有' if server.get('token') else '空'}")
        if server.get("enable") and server.get("port") == DEFAULT_V2_SEND_PORT:
            send_ok = True
    if not send_ok:
        print(f"  ✗ 没有启用的 httpServer 监听 {DEFAULT_V2_SEND_PORT} —— v2 将无法发送消息")
        ok = False
    else:
        print("  ✓ 发送通道就绪")

    # ---- 接收通道: NapCat 把事件推到 v2 ----
    print("\n【接收通道】事件上报目标(应有一条指向 v2 的 /qq/webhook)")
    recv_ok = False
    stale: list[str] = []
    for client in network.get("httpClients") or []:
        enabled = bool(client.get("enable"))
        url = str(client.get("url") or "")
        mark = "启用" if enabled else "停用"
        print(f"  - {client.get('name')}: {mark} -> {url}")
        if enabled and url == DEFAULT_V2_WEBHOOK:
            recv_ok = True
        # 提醒仍然启用、但指向已废弃端口的条目(旧 Java 服务已移除)
        if enabled and ("8091" in url or "8093" in url):
            stale.append(f"{client.get('name')} -> {url}")

    if not recv_ok:
        print(f"  ✗ 没有启用的 httpClient 指向 {DEFAULT_V2_WEBHOOK} —— v2 收不到消息")
        ok = False
    else:
        print("  ✓ 接收通道就绪")

    if stale:
        print("\n⚠ 仍在启用但指向已废弃服务的条目(建议停用,避免无效重试):")
        for item in stale:
            print(f"    {item}")

    # ---- 消息格式 ----
    print("\n【消息格式】v2 的解析器同时支持 array 与 string,但推荐 array(保留结构)")
    formats = {
        str(c.get("messagePostFormat"))
        for c in (network.get("httpClients") or [])
        if c.get("enable")
    }
    print(f"  当前上报格式: {', '.join(sorted(formats)) or '未设置'}")

    print("\n" + ("✓ 配置检查通过" if ok else "✗ 配置有问题,请按上面提示调整"))
    print("提示: 修改配置后需要重启 NapCat 才生效。")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
