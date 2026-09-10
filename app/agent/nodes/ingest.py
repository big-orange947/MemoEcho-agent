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
#
# 长期记忆: 本节点**不写**。逐条写会把记忆碎成一堆"嗯""好的",所以
# 长期记忆改由攒批写入 —— 消息先落在 messages 表,由 app/batches.py
# (调度器低频驱动)按窗口总结后再写入 Doppel。
# =============================================================================

from __future__ import annotations

from typing import Any

from ...recorder import build_message
from ...services import conversations as conversations_service


async def run(state: dict[str, Any]) -> dict[str, Any]:
    """处理当前事件: 分流 → 归一化 → 去重 → 写入历史。

    长期记忆不在这里写(见文件头注释): 本节点只负责把消息落进 messages 表,
    攒批总结由 app/batches.py 负责。
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
    # 归一化与"仅记录"路径共用同一实现(app/recorder.py),避免两处形态漂移。
    goal = state.get("goal") or {}
    message = build_message(event, goal_id=str(goal.get("id") or ""))
    msg_id = str(message["id"])

    # ---------------------------------------------------------------- 3. 去重 + 落库
    # 幂等的**真正含义**: 同一条消息重复进来(平台重推)时,
    # 不只是"不重复入库",还必须**整轮跳过** —— 否则 agent 会重新推理
    # 并再回复一次,用户就收到两条一样的回复(实测出现过)。
    #
    # 判断依据: 该会话里是否已存在这个 message_id。
    #   · 存在 → 这条消息处理过 → 返回 duplicate=True,由条件边直接结束;
    #   · 不存在 → 正常流程(落库 → 推理)。
    #
    # 例外: instruction(调度指令)用带随机后缀的 ID,且即便重复也更希望
    # 重新执行(调用方明确要求),所以不参与短路。
    if kind != "instruction" and conversations_service.message_exists(conversation_id, msg_id):
        return {
            "conversation_id": conversation_id,
            "duplicate": True,   # 条件边据此结束本轮(不推理、不回复)
        }

    conversations_service.add_message(conversation_id, message)

    # ---------------------------------------------------------------- 4. 更新状态
    # 返回新的 conversation_id(首次创建时才有值)和追加后的消息列表。
    # add_messages reducer 会自动按 ID 去重,这里直接返回 [message] 即可。
    return {
        "conversation_id": conversation_id,
        "duplicate": False,
        "messages": [message],
    }
