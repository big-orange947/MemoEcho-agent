# =============================================================================
# nodes/ingest.py - 事件归一化节点(图的入口)
# -----------------------------------------------------------------------------
# 职责: 把外部事件(QQ 消息/桌面命令)变成"消息",写进会话历史。
#
# 为什么需要这一步:
#   1. 统一入口 —— 后面所有节点只处理"消息",不关心来源是 QQ 还是桌面;
#   2. 去重 —— 相同平台消息 ID 再次进来时跳过,防止重复处理(幂等);
#   3. 落库 —— 消息写进 messages 表,checkpoint 恢复时历史完整。
#
# 输入 State: conversation_id, event
# 输出 State: messages(追加一条归一化消息)
# =============================================================================

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from ...services import conversations as conversations_service


def run(state: dict[str, Any]) -> dict[str, Any]:
    """处理当前事件: 归一化 → 去重 → 写入历史。"""
    event = state.get("event") or {}
    conversation_id = state.get("conversation_id") or ""

    # ---------------------------------------------------------------- 0. 定时唤醒事件
    # timer 事件不是"对方说的话",不写进对话历史;它只是告诉 agent
    # "你之前设的等待到期了,该继续推进了"。处理方式:
    #   把唤醒原因放进 working_memory,reason 节点拼进系统提示。
    if event.get("event_type") == "timer":
        working = dict(state.get("working_memory") or {})
        working["wakeup_reason"] = event.get("text") or "定时唤醒"
        return {
            "conversation_id": conversation_id,
            "working_memory": working,
        }

    # ---------------------------------------------------------------- 1. 定位会话
    # 如果 state 里还没有会话 ID(首次事件),按 platform/chat_type/external_id
    # 查库;不存在则自动创建(首次对话)。
    if not conversation_id:
        conversation_id = conversations_service.ensure_conversation(
            platform=event.get("platform", "qq"),
            chat_type=event.get("chat_type", "private"),
            external_id=event.get("external_id", ""),
        )

    # ---------------------------------------------------------------- 2. 归一化消息
    # 把事件转成 messages 表/历史里的统一消息形态。
    # 注意: 机器人自己发出的消息(is_self)也记录(供上下文理解),但不会触发代理。
    msg_id = event.get("event_id") or uuid.uuid4().hex
    role = "assistant" if event.get("is_self") else "user"
    source = "outbound" if event.get("is_self") else "inbound"

    message = {
        "id": msg_id,
        "role": role,
        "content": event.get("text", ""),
        "source": source,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "goal_id": state.get("goal", {}).get("id", "") if state.get("goal") else "",
    }

    # ---------------------------------------------------------------- 3. 去重 + 落库
    # 已存在相同消息 ID 说明是重投/回显,跳过写入。
    if not conversations_service.message_exists(conversation_id, msg_id):
        conversations_service.add_message(conversation_id, message)

    # ---------------------------------------------------------------- 4. 更新状态
    # 返回新的 conversation_id(首次创建时才有值)和追加后的消息列表。
    # add_messages reducer 会自动按 ID 去重,这里直接返回 [message] 即可。
    return {
        "conversation_id": conversation_id,
        "messages": [message],
    }
