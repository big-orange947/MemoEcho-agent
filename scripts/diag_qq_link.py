# -*- coding: utf-8 -*-
"""真号链路诊断: 发送通道 + 接收通道 + 全事件类型。

用法: uv run python scripts/diag_qq_link.py [--send-to <QQ号>]

不传 --send-to 时只做只读检查(不发任何消息)。
"""
from __future__ import annotations

import argparse
import io
import sys

import httpx

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

V2 = "http://127.0.0.1:8000"
NAPCAT = "http://127.0.0.1:3011"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--send-to", default="", help="测试发送目标的 QQ 号(留空则只做只读检查)")
    args = parser.parse_args()

    c = httpx.Client(timeout=30)
    problems: list[str] = []

    # ---- 1. NapCat ----
    print("=== 1. NapCat (发送通道) ===")
    try:
        r = c.post(f"{NAPCAT}/get_login_info").json()
        if r.get("retcode") == 0:
            print(f"  ✓ 已登录: QQ {r['data']['user_id']} / {r['data']['nickname']}")
        else:
            print(f"  ✗ 接口异常: {r}")
            problems.append("NapCat get_login_info 返回异常")
    except Exception as exc:
        print(f"  ✗ 无法连接: {exc}")
        problems.append("NapCat 3011 无响应")

    # ---- 2. v2 服务 ----
    print("\n=== 2. v2 服务 ===")
    try:
        convs = c.get(f"{V2}/api/conversations").json()
        print(f"  ✓ API 可用(会话数 {len(convs)})")
    except Exception as exc:
        print(f"  ✗ 无法连接: {exc}")
        problems.append("v2 API 不可用")
        print("\n链路不完整,后续检查跳过。")
        return 1

    # ---- 3. 事件审计(看 webhook 是否真的收到过事件) ----
    print("\n=== 3. 事件审计(2026-09-10 起) ===")
    events = c.get(f"{V2}/api/events", params={"limit": 10}).json()
    if not events:
        print("  (暂无事件 —— 说明还没有真实消息进来)")
    for e in events:
        print(f"  [{e['event_type']}] respond={e['should_respond']} | {e['summary'][:60]}")

    stats = c.get(f"{V2}/api/events/stats").json()
    print(f"  分类统计: {stats}")

    # ---- 4. 发送测试(可选) ----
    if args.send_to:
        print(f"\n=== 4. 发送测试 → {args.send_to} ===")
        # 通过 v2 的发送路径(模拟 agent 回复)
        payload = {
            "post_type": "message",
            "message_type": "private",
            "user_id": int(args.send_to),
            "message_id": 999001,
            "message": [{"type": "text", "data": {"text": "链路自检: 收到请忽略"}}],
        }
        # 直接调 NapCat 发(验证发送通道本身)
        resp = c.post(
            f"{NAPCAT}/send_private_msg",
            json={"user_id": int(args.send_to), "message": "链路自检: 收到请忽略(v2 发送通道测试)"},
        ).json()
        if resp.get("retcode") == 0:
            print(f"  ✓ 发送成功 (message_id={resp.get('data', {}).get('message_id')})")
        else:
            print(f"  ✗ 发送失败: {resp}")
            problems.append("NapCat 发送消息失败")

    print("\n" + ("✓ 链路检查通过" if not problems else "✗ 发现问题:\n  - " + "\n  - ".join(problems)))
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
