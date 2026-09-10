# -*- coding: utf-8 -*-
"""临时测试: 第二轮对话(checkpoint 恢复)+ goal 命令。"""
import io
import sys
import time

import httpx

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BASE = "http://127.0.0.1:8000"
CONV = "smoke-thread-1"
c = httpx.Client(timeout=240)

# 第二轮: 验证 checkpoint 恢复(LLM 应能看到第一轮对话)
r = c.post(f"{BASE}/api/conversations/{CONV}/messages", json={"text": "我刚才问了你什么问题?"})
print("round2 POST:", r.status_code)
time.sleep(14)
msgs = c.get(f"{BASE}/api/conversations/{CONV}/messages").json()
print("round2 messages:")
for m in msgs[-2:]:
    print("  [%s] %s" % (m["role"], m["content"][:150]))

# goal 命令
r3 = c.post(f"{BASE}/api/conversations/{CONV}/goal", json={"objective": "记住我的姓是王"})
print("\ngoal POST:", r3.status_code, r3.text[:200])
goals = c.get(f"{BASE}/api/conversations/{CONV}/goals").json()
print("goals:", [(g["objective"], g["status"]) for g in goals])
