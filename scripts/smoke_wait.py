# -*- coding: utf-8 -*-
"""临时验证: wait 工具端到端(真实链路)。

流程:
  1. 发消息给 agent:"等 5 秒后提醒我喝水"(LLM 应调用 wait 工具);
  2. 检查 scheduled_events 表是否有 pending 记录;
  3. 等 scheduler 轮询到期 → 发 timer 事件 → 图被唤醒执行;
  4. 观察唤醒后会话的消息变化。
"""
import io
import sqlite3
import sys
import time

import httpx

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BASE = "http://127.0.0.1:8000"
CONV = "wait-thread-2"
c = httpx.Client(timeout=240)


def peek_schedules(conv: str) -> list:
    conn = sqlite3.connect("data/memo-echo.db")
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, due_at, note, status FROM scheduled_events WHERE conversation_id=? ORDER BY created_at",
        (conv,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# 1. 发消息
r = c.post(f"{BASE}/api/conversations/{CONV}/messages", json={"text": "等 5 秒后提醒我喝水"})
print("POST:", r.status_code)
time.sleep(3)

# 2. 检查 wait 工具是否被调用(应出现 pending 记录)
schedules = peek_schedules(CONV)
print("\n登记在案的唤醒:")
for s in schedules:
    print("  ", s)

# 3. 等 scheduler 触发(5 秒到期 + 轮询间隔)
print("\n等待 scheduler 唤醒...")
time.sleep(10)

# 4. 看唤醒后的消息
msgs = c.get(f"{BASE}/api/conversations/{CONV}/messages").json()
print(f"\n消息数: {len(msgs)}")
for m in msgs:
    print("  [%s/%s] %s" % (m["role"], m["source"], m["content"][:120]))

# 5. 唤醒记录应已 fired
schedules = peek_schedules(CONV)
print("\n触发后的唤醒记录:")
for s in schedules:
    print("  ", s)