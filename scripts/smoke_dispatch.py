# -*- coding: utf-8 -*-
"""调度入口真实链路冒烟(需要运行中的服务 + 真实 LLM)。

模拟"主 agent 派活给 Memo Echo"的完整流程:
  1. send_message: 直接发送(不给 agent 决策)
  2. note:         仅记录背景
  3. task:         委托型任务(建 goal,agent 自主推进)
  4. 幂等:         重复投递同一 key 不重复执行
  5. 查询任务产物
"""
import io
import sys
import time

import httpx

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BASE = "http://127.0.0.1:8000"
c = httpx.Client(timeout=240)


def show(title: str) -> None:
    print(f"\n{'=' * 60}\n{title}\n{'=' * 60}")


# 1. send_message: 直接发送
show("1. send_message —— 直接发送(不经 agent)")
r = c.post(f"{BASE}/api/dispatch", json={
    "caller": "main-agent",
    "kind": "send_message",
    "target": {"conversation_id": "agent-task-demo"},
    "text": "【来自主 agent 的中转消息】",
    "result_mode": "poll",
})
print("POST:", r.status_code)
task1 = r.json()
print(f"task_id={task1['task_id']} status={task1['status']}")
time.sleep(1)
detail = c.get(f"{BASE}/api/dispatch/{task1['task_id']}").json()
print(f"查询: status={detail['status']} result={detail['result']}")

msgs = c.get(f"{BASE}/api/conversations/agent-task-demo/messages").json()
print(f"会话消息数: {len(msgs)}")
for m in msgs:
    print(f"  [{m['role']}/{m['source']}] {m['content'][:60]}")

# 2. note: 仅记录
show("2. note —— 仅记录背景")
r = c.post(f"{BASE}/api/dispatch", json={
    "caller": "main-agent",
    "kind": "note",
    "target": {"conversation_id": "agent-task-demo"},
    "text": "背景: 这是主 agent 的中转会话",
})
print("POST:", r.status_code, "| status:", r.json()["status"])

# 3. task: 委托型(建 goal + agent 推进)
show("3. task —— 委托任务(建 goal,agent 自主推进)")
r = c.post(f"{BASE}/api/dispatch", json={
    "caller": "main-agent",
    "kind": "task",
    "target": {"conversation_id": "agent-task-demo"},
    "instruction": "请简短地打个招呼就好",
    "result_mode": "poll",
})
print("POST:", r.status_code)
task3 = r.json()
print(f"task_id={task3['task_id']}")
time.sleep(15)  # 等 agent 跑完(真实 LLM)

detail3 = c.get(f"{BASE}/api/dispatch/{task3['task_id']}").json()
print(f"任务状态: {detail3['status']}")
print(f"产物: {detail3['result']}")

goals = c.get(f"{BASE}/api/conversations/agent-task-demo/goals").json()
print(f"目标数: {len(goals)}")
for g in goals:
    print(f"  [{g['status']}] {g['objective'][:50]} | 进度: {g['progress'][:40]}")

# 4. 幂等
show("4. 幂等 —— 重复投递同一 key")
payload = {
    "kind": "send_message",
    "target": {"conversation_id": "agent-idem-demo"},
    "text": "这条只应出现一次",
    "idempotency_key": "demo-key-001",
}
r1 = c.post(f"{BASE}/api/dispatch", json=payload).json()
r2 = c.post(f"{BASE}/api/dispatch", json=payload).json()
print(f"第一次: task_id={r1['task_id'][:16]} duplicated={r1.get('duplicated', False)}")
print(f"第二次: task_id={r2['task_id'][:16]} duplicated={r2.get('duplicated', False)}")
print(f"是同一任务: {r1['task_id'] == r2['task_id']}")

msgs = c.get(f"{BASE}/api/conversations/agent-idem-demo/messages").json()
print(f"实际发送消息数: {len(msgs)}（应为 1）")

# 5. 任务列表
show("5. 任务列表")
tasks = c.get(f"{BASE}/api/dispatch", params={"limit": 10}).json()
for t in tasks:
    print(f"  [{t['status']:8}] {t['kind']:14} caller={t['caller']:12} {t['task_id'][:12]}")
