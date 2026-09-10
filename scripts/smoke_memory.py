# -*- coding: utf-8 -*-
"""记忆功能端到端冒烟(需要运行中的服务 + 真实 LLM + Doppel)。

验证"agent 会记住并主动引用"这条完整链路:
  1. 告诉 agent 一个事实(如"我下周三去北京出差");
  2. 换一个话题再问,看它是否能召回;
  3. 检查记忆库确实写入了。
"""
import io
import sys
import time

import httpx

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BASE = "http://127.0.0.1:8000"
CONV = "memory-demo"
c = httpx.Client(timeout=240)


def ask(text: str, wait: int = 14) -> str:
    """发一条消息并取回 agent 的回复。"""
    r = c.post(f"{BASE}/api/conversations/{CONV}/messages", json={"text": text})
    if r.status_code != 200:
        return f"(请求失败 {r.status_code})"
    time.sleep(wait)
    msgs = c.get(f"{BASE}/api/conversations/{CONV}/messages").json()
    # 取最后一条 assistant 消息
    for m in reversed(msgs):
        if m["role"] == "assistant":
            return m["content"]
    return "(无回复)"


print("=" * 60)
print("1. 告诉 agent 一个事实")
print("=" * 60)
reply = ask("记住一下:我下周三要去北京出差三天")
print(f"agent: {reply}")

print()
print("=" * 60)
print("2. 换个话题(测试是否还记得)")
print("=" * 60)
reply = ask("对了,我下周有什么安排吗?")
print(f"agent: {reply}")

print()
print("=" * 60)
print("3. 记忆库检查")
print("=" * 60)
import asyncio
import sys as _sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in _sys.path:
    _sys.path.insert(0, str(ROOT))


async def check_memory() -> None:
    from app import memory
    from app.db import init_db
    from app.services import conversations as convs

    init_db()
    conversation = convs.get_conversation(CONV)
    if not conversation:
        print("  会话不存在")
        return

    scope_conv = convs.find_conversation("desktop", "thread", CONV) or conversation
    for query in ("北京出差", "下周安排"):
        hits = await memory.recall(scope_conv, query, limit=5)
        print(f'  查询「{query}」→ {len(hits)} 条')
        for h in hits:
            print(f"    [{h['actor']}] {h['fact'][:70]}")

    await memory.close_client()


asyncio.run(check_memory())
