# =============================================================================
# recorder.py - 消息记录器(不跑图的那条路径)
# -----------------------------------------------------------------------------
# 为什么需要单独一条路径:
#   现状里"把消息写进历史"发生在**图里**(nodes/ingest.py) —— 不跑图 = 不记录。
#   于是两件事做不到:
#     · 只监视不回复的会话,消息全丢(没有数据可总结、可上报);
#     · 号主自己在手机上发的消息(message_sent)从不入库,agent 永远不知道
#       "这场对话里其实还有别人/号主说的话"。
#   本模块提供"只记录、不推理"的最小动作,零模型成本。
#
# 与 ingest 的分工(重要,别写反):
#   · 跑图的路径(显式指令 / auto 回复) → 记录仍归 ingest 负责。
#     不能在这里预记录 —— ingest 会判定"消息已存在"而短路到 END,
#     回复就丢了(实测踩过:幂等保护把正常回复吃掉了)。
#   · 不跑图的路径(仅监视) → 走本模块,只落 messages 表。
#
# 长期记忆: 本模块**不写**。监视会话的记忆统一走"攒批总结"
# (见 app/batches.py: 攒够条数或消息静止后总结一批再写),
# 逐条写会把记忆碎成一堆"嗯""好的"。
# =============================================================================

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from .services import conversations as conversations_service

# 能进对话历史的 kind。notice/request 这类平台流水不进 ——
# 它们不是"人说的话",进去只会污染上下文。
RECORDABLE_KINDS = frozenset({"message", "message_sent", "instruction", "command"})

# 各 kind 对应的消息角色与来源标记
# role:   user(对方) / assistant(自己) / system(系统记录)
# source: inbound / outbound / system
_KIND_ROLE: dict[str, tuple[str, str]] = {
    "message": ("user", "inbound"),
    "instruction": ("user", "inbound"),
    "command": ("user", "inbound"),
    "message_sent": ("assistant", "outbound"),
    "notice": ("system", "system"),
    "request": ("system", "system"),
    "system": ("system", "system"),
}


def build_message(event: dict[str, Any], *, goal_id: str = "") -> dict[str, Any]:
    """把事件归一化成一条消息(ingest 与本模块共用,避免两处形态漂移)。

    事件 ID 直接当消息 ID: 平台重推同一事件时 ID 相同 → 天然幂等。
    """
    kind = str(event.get("kind") or "message")
    role, source = _KIND_ROLE.get(kind, ("user", "inbound"))

    # 机器人自己发出的消息一律按 assistant 记
    if event.get("is_self"):
        role, source = "assistant", "outbound"

    return {
        "id": event.get("event_id") or uuid.uuid4().hex,
        "role": role,
        "content": event.get("text", ""),
        "source": source,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "goal_id": goal_id,
        # 结构化内容段原样留存: 供后续"取图片/引用回复"等高级处理,
        # 也便于事后核对"当时到底收到了什么"(text 是渲染结果,细节会有损失)
        "content_parts": event.get("content") or [],
        "sender_name": event.get("sender_name") or "",
    }


async def record(conversation_id: str, event: dict[str, Any], *, goal_id: str = "") -> dict[str, Any]:
    """把一条事件作为消息写进会话历史(幂等,不推理、不调模型)。

    返回: {"recorded": bool, "duplicate": bool, "message_id": str, "reason": str}
      recorded=False 的常见原因: kind 不可记录(通知/请求类)、文本为空。
    """
    kind = str(event.get("kind") or "message")
    if kind not in RECORDABLE_KINDS:
        return {"recorded": False, "duplicate": False, "message_id": "", "reason": f"kind 不记录: {kind}"}

    if not conversation_id:
        return {"recorded": False, "duplicate": False, "message_id": "", "reason": "缺少会话 ID"}

    message = build_message(event, goal_id=goal_id)
    message_id = str(message["id"])

    # 文本与内容段都为空的消息没有记录价值(如纯撤回占位)
    if not str(message.get("content") or "").strip() and not message.get("content_parts"):
        return {"recorded": False, "duplicate": False, "message_id": message_id, "reason": "空消息"}

    if conversations_service.message_exists(conversation_id, message_id):
        return {"recorded": False, "duplicate": True, "message_id": message_id, "reason": "已存在"}

    conversations_service.add_message(conversation_id, message)
    return {"recorded": True, "duplicate": False, "message_id": message_id, "reason": ""}
