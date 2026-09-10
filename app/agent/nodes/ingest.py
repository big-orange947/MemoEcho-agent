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


async def run(state: dict[str, Any]) -> dict[str, Any]:
    """处理当前事件: 分流 → 归一化 → 去重 → 写入历史 + 长期记忆。

    注意: 这是异步节点(记忆写入是异步 IO),LangGraph 原生支持。
    """
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
    # 幂等的**真正含义**: 同一条消息重复进来(平台重推)时,
    # 不只是"不重复入库",还必须**整轮跳过** —— 否则 agent 会重新推理
    # 并再回复一次,用户就收到两条一样的回复(实测出现过)。
    #
    # 判断依据: 该会话里是否已存在这个 message_id。
    #   · 存在 → 这条消息处理过 → 返回 duplicate=True,由条件边直接结束;
    #   · 不存在 → 正常流程(落库 → 记忆 → 推理)。
    #
    # 例外: instruction(调度指令)用带随机后缀的 ID,且即便重复也更希望
    # 重新执行(调用方明确要求),所以不参与短路。
    if kind != "instruction" and conversations_service.message_exists(conversation_id, msg_id):
        return {
            "conversation_id": conversation_id,
            "duplicate": True,   # 条件边据此结束本轮(不推理、不回复)
        }

    conversations_service.add_message(conversation_id, message)

    # ---------------------------------------------------------------- 4. 写入长期记忆
    # 把这条消息喂给 Doppel(它会做抽取/合并,形成长期事实)。
    # 注意: 这里只对**对话消息**调用 —— 通知/请求等系统事件不入记忆,
    # 否则"好友撤回了一条消息"这种流水会污染记忆语义。
    # 写入是 best-effort: 失败只记日志,不影响对话。
    if kind in ("message", "instruction", "message_sent"):
        await _remember(conversation_id, message)

    # ---------------------------------------------------------------- 5. 更新状态
    # 返回新的 conversation_id(首次创建时才有值)和追加后的消息列表。
    # add_messages reducer 会自动按 ID 去重,这里直接返回 [message] 即可。
    return {
        "conversation_id": conversation_id,
        "duplicate": False,
        "messages": [message],
    }


async def _remember(conversation_id: str, message: dict[str, Any]) -> None:
    """把消息写入长期记忆(best-effort)。

    为什么本节点是 async:
      记忆写入是异步 IO,而同步节点跑在线程池里(拿不到事件循环),
      无法 await。做成异步节点后可以直接 await,逻辑最直白。
      代价是写入会稍许拉长这一轮执行 —— Doppel 的 SQLite 写入是毫秒级,
      相比后面要跑的 LLM 调用可以忽略。

    失败降级: memory.remember_message 内部已吞掉异常并返回 False,
    这里不再判断返回值 —— 记忆写没写成功,都不该影响这次对话。
    """
    from ... import memory

    conversation = conversations_service.get_conversation(conversation_id) or {}
    if not conversation:
        return
    await memory.remember_message(
        conversation,
        role=str(message.get("role") or ""),
        content=str(message.get("content") or ""),
        created_at=str(message.get("created_at") or ""),
        message_id=str(message.get("id") or ""),
        source=str(message.get("source") or ""),
        parts=message.get("content_parts") or None,
    )
