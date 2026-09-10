# -*- coding: utf-8 -*-
"""临时冒烟测试: 桌面端消息 → agent 六节点图 → 落库 → 取回。

用 httpx 直接发 UTF-8 请求,避免 PowerShell 5.1 的 ASCII 编码问题。
"""
import io
import sys
import time

import httpx

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BASE = "http://127.0.0.1:8000"
CONV = "smoke-thread-1"


def main() -> None:
    client = httpx.Client(timeout=240)

    # 1. 发一条中文消息(触发完整图执行: ingest→retrieve→reason→reflect→finalize)
    r = client.post(f"{BASE}/api/conversations/{CONV}/messages", json={"text": "你好,你是谁?"})
    print("POST /messages:", r.status_code, r.text[:200])
    assert r.status_code == 200, r.text

    # 2. 等 agent 跑完(异步发布,轮询消息数)
    for _ in range(60):
        time.sleep(2)
        msgs = client.get(f"{BASE}/api/conversations/{CONV}/messages").json()
        if len(msgs) >= 2:
            break
    print(f"\n消息数: {len(msgs)}")
    for m in msgs:
        print(f"  [{m['role']}/{m['source']}] {m['content'][:120]}")

    # 3. 断言: 有一条入站 + 一条出站,且出站是纯文本(不是 JSON 信封)
    outbound = [m for m in msgs if m["source"] == "outbound"]
    inbound = [m for m in msgs if m["source"] == "inbound"]
    assert inbound, "缺少入站消息"
    assert outbound, "缺少出站回复"
    assert not outbound[-1]["content"].strip().startswith("{"), (
        f"回复仍是 JSON 信封: {outbound[-1]['content'][:120]}"
    )
    print("\nSMOKE OK ✅  入站:", repr(inbound[-1]["content"]))
    print("SMOKE OK ✅  出站:", repr(outbound[-1]["content"]))


if __name__ == "__main__":
    main()
