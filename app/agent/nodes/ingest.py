# =============================================================================
# nodes/ingest.py - 事件归一化节点(图的入口)
# -----------------------------------------------------------------------------
# 职责: 把外部事件(QQ 消息/桌面命令/调度指令/定时唤醒)变成"消息",写进会话历史。
#
# 为什么需要这一步:
#   1. 统一入口 —— 后面所有节点只处理"消息",不关心来源是 QQ 还是桌面;
#   2. 分流 —— 不同 kind 的事件处理方式不同(见下);
#   3. 去重 —— 相同事件 ID 再次进来时跳过,防止重复处理(幂等);
#   4. 落库 —— 消息写进 messages 表,checkpoint 恢复时历史完整。
#
# 按 kind 分流规则:
#   message      → 作为对方消息写入历史(user/inbound),参与后续推理
#   instruction  → 作为指令写入历史(user/inbound),语义等于"用户说的话"
#   message_sent → 作为自己说的话写入历史(assistant/outbound),不触发回复
#   timer        → **不写历史**,只把唤醒原因放进工作记忆(它不是"人说的话")
#   notice/request → 通常不会走到这里(should_respond=False,由审计层记录);
#                    若进入,记为系统消息保留痕迹,但不污染对话
#
# 输入 State: conversation_id, event
# 输出 State: messages(追加一条归一化消息)/ working_memory(定时唤醒时)
# =============================================================================

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from ...services import conversations as conversations_service

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


def run(state: dict[str, Any]) -> dict[str, Any]:
    """处理当前事件: 分流 → 归一化 → 去重 → 写入历史。"""
    event = state.get("event") or {}
    conversation_id = state.get("conversation_id") or ""
    kind = str(event.get("kind") or "message")

    # ---------------------------------------------------------------- 0. 定时唤醒
    # timer 事件不是"对方说的话",不能写进对话历史 —— 否则 LLM 会把
    # "提醒用户喝水"当成对方发来的消息,语义就错了。
    # 正确做法: 把唤醒原因放进工作记忆,reason 节点拼进系统提示。
    if kind == "timer":
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
    # 事件 ID 直接用作消息 ID: 平台重推同一事件时消息 ID 相同 → 天然幂等。
    msg_id = event.get("event_id") or uuid.uuid4().hex

    # 角色与来源按 kind 决定(未知 kind 按"对方消息"兜底)
    role, source = _KIND_ROLE.get(kind, ("user", "inbound"))

    # 机器人自己发出的消息一律按 assistant 记
    # (message_sent 已映射为 assistant;这里兜底其它来源的 is_self 事件)
    if event.get("is_self"):
        role, source = "assistant", "outbound"

    message = {
        "id": msg_id,
        "role": role,
        "content": event.get("text", ""),
        "source": source,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "goal_id": state.get("goal", {}).get("id", "") if state.get("goal") else "",
        # 结构化内容段原样留存: 供后续"取图片/引用回复"等高级处理,
        # 也便于事后核对"当时到底收到了什么"(text 是渲染结果,细节会有损失)
        "content_parts": event.get("content") or [],
        "sender_name": event.get("sender_name") or "",
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
