# -*- coding: utf-8 -*-
"""实测: agent 通过工具给别人发消息(转告能力的核心)。

用法: uv run python scripts/smoke_send_tool.py --to <QQ号>
"""
import argparse
import io
import sys
import time

import httpx

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BASE = "http://127.0.0.1:8000"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--to", required=True, help="让 agent 发消息的目标 QQ 号")
    parser.add_argument("--conv", default="send-tool-demo", help="触发用的会话 ID")
    args = parser.parse_args()

    c = httpx.Client(timeout=240)

    # 触发: 明确要求 agent 用工具给指定 QQ 发一条消息
    prompt = f"请用工具给 QQ {args.to} 发一条消息,内容是:这是一条工具发送测试"
    print(f"触发消息: {prompt}")
    r = c.post(f"{BASE}/api/conversations/{args.conv}/messages", json={"text": prompt})
    print("POST:", r.status_code)

    print("等待 agent 执行(含工具调用)...")
    time.sleep(20)

    # 看触发会话的消息(agent 的回复 + 工具调用痕迹)
    msgs = c.get(f"{BASE}/api/conversations/{args.conv}/messages").json()
    print(f"\n触发会话消息数: {len(msgs)}")
    for m in msgs:
        print(f"  [{m['role']}] {m['content'][:120]}")

    # 关键验证: 目标联系人的会话里应该有出站记录(说明工具真的发出去了)
    print(f"\n=== 目标会话({args.to})的记录 ===")
    target = c.get(f"{BASE}/api/conversations").json()
    hit = [x for x in target if x["external_id"] == str(args.to)]
    if not hit:
        print("  ✗ 目标会话不存在 —— 工具很可能没有执行发送")
        return 1

    target_msgs = c.get(f"{BASE}/api/conversations/{hit[0]['id']}/messages").json()
    outbound = [m for m in target_msgs if m["source"] == "outbound"]
    print(f"  出站消息数: {len(outbound)}")
    for m in outbound:
        print(f"    [{m['source']}] {m['content'][:100]}")

    if outbound:
        print("\n✓ 工具发送成功: 目标会话里有出站记录")
        return 0
    print("\n✗ 目标会话里没有出站记录 —— 发送未生效")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
