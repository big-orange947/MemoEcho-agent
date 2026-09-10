# =============================================================================
# api/sse.py - SSE 实时推送
# -----------------------------------------------------------------------------
# 桌面端需要"实时看到 agent 的回复/进度",用 SSE(Server-Sent Events)实现:
#   1. 桌面端打开 GET /api/stream 长连接;
#   2. agent 每产生一条消息/进度,通过 push() 推给所有连接的客户端;
#   3. 前端用 EventSource 接收,按 event 类型处理。
#
# 实现要点:
#   - 用 asyncio.Queue 给每个连接缓冲事件,避免并发写同一个 response;
#   - 广播用简单的"全局订阅者列表"(单进程内足够);
#   - 消息格式: "event: <type>\ndata: <json>\n\n"。
# =============================================================================

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

router = APIRouter(prefix="/api")

# 全局订阅者: {asyncio.Queue: None}
_subscribers: set[asyncio.Queue] = set()


async def push(event_type: str, data: dict[str, Any]) -> None:
    """向所有订阅的桌面端广播一个事件。"""
    if not _subscribers:
        return
    payload = f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
    for queue in list(_subscribers):
        queue.put_nowait(payload)


@router.get("/stream")
async def stream():
    """SSE 长连接端点。前端:
        const es = new EventSource('/api/stream');
        es.addEventListener('reply', e => ...);
        es.addEventListener('progress', e => ...);
    """
    queue: asyncio.Queue = asyncio.Queue()
    _subscribers.add(queue)

    async def event_generator():
        try:
            # 连接存活期间不断从队列取事件下发
            while True:
                payload = await queue.get()
                yield payload
        except asyncio.CancelledError:
            pass
        finally:
            # 断开时清理订阅,防止泄漏
            _subscribers.discard(queue)

    return StreamingResponse(event_generator(), media_type="text/event-stream")
