# -*- coding: utf-8 -*-
"""端到端冒烟: 消息模型重构后的真实链路验证(需要运行中的服务 + 真实 LLM)。

覆盖:
  1. 桌面端消息 → 六节点图 → 回复(真实 LLM);
  2. webhook 数组消息 → 解析渲染 → 回复;
  3. 通知事件 → 只审计不回复;
  4. 自发消息回显 → 只审计不回复;
  5. 审计端点可查询。
"""
import io
import sys
import time

import httpx

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BASE = "http://127.0.0.1:8000"
c = httpx.Client(timeout=240)

# 1. 桌面端消息
print("=== 1. 桌面端消息 ===")
r = c.post(f"{BASE}/api/conversations/smoke-v2-1/messages", json={"text": "你好，请用一句话介绍你自己"})
print("POST:", r.status_code)
time.sleep(12)
msgs = c.get(f"{BASE}/api/conversations/smoke-v2-1/messages").json()
for m in msgs:
    print("  [%s/%s] %s" % (m["role"], m["source"], m["content"][:100]))

# 2. webhook 数组消息(@ + 文本 + 图片)
print("\n=== 2. webhook 数组消息 ===")
payload = {
    "post_type": "message",
    "message_type": "private",
    "user_id": 90001,
    "message_id": 70001,
    "sender": {"nickname": "测试好友"},
    "message": [
        {"type": "text", "data": {"text": "帮我看下这个"}},
        {"type": "image", "data": {"file": "a.jpg", "url": "http://x/a.jpg"}},
    ],
}
r = c.post(f"{BASE}/qq/webhook", json=payload)
print("webhook:", r.status_code, r.json())
time.sleep(12)
convs = c.get(f"{BASE}/api/conversations").json()
target = next((x for x in convs if x["external_id"] == "90001"), None)
if target:
    msgs = c.get(f"{BASE}/api/conversations/{target['id']}/messages").json()
    for m in msgs:
        print("  [%s/%s] %s" % (m["role"], m["source"], m["content"][:100]))
else:
    print("  !! 会话未创建")

# 3. 通知事件(只审计)
print("\n=== 3. 通知事件(应只审计,不回复) ===")
r = c.post(f"{BASE}/qq/webhook", json={
    "post_type": "notice", "notice_type": "friend_recall",
    "user_id": 90001, "message_id": 70002,
})
print("webhook:", r.status_code)
time.sleep(2)
events = c.get(f"{BASE}/api/events", params={"kind": "notice"}).json()
print("  审计记录:", [(e["summary"], e["should_respond"]) for e in events])

# 4. 自发回显(只审计)
print("\n=== 4. 自发消息回显(应只审计,不回复) ===")
r = c.post(f"{BASE}/qq/webhook", json={
    "post_type": "message_sent", "message_type": "private",
    "user_id": 90001, "self_id": 3969785168, "message_id": 70003,
    "message": [{"type": "text", "data": {"text": "我自己发的消息"}}],
})
print("webhook:", r.status_code)
time.sleep(2)

# 5. 审计统计
print("\n=== 5. 审计统计 ===")
print("  ", c.get(f"{BASE}/api/events/stats").json())

# 6. 幂等验证: 重推同一条消息
print("\n=== 6. 幂等(重推同一 message_id) ===")
c.post(f"{BASE}/qq/webhook", json=payload)  # 与第 2 步相同 message_id
time.sleep(3)
if target:
    msgs2 = c.get(f"{BASE}/api/conversations/{target['id']}/messages").json()
    inbound = [m for m in msgs2 if m["source"] == "inbound"]
    print(f"  入站消息数: {len(inbound)}（应为 1，说明重推被去重）")
