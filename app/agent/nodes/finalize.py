# =============================================================================
# nodes/finalize.py - 收尾节点(图的出口)
# -----------------------------------------------------------------------------
# 职责: 把整轮执行的成果落库 + 推送出去:
#   1. 把最终回复写入 messages(assistant 消息,落库持久化);
#   2. 更新目标状态(如果有 goal,按 reflect 评估结果更新);
#   3. 调用"发送器"(QQ 桥/桌面端 SSE)把回复真正发出去。
#
# 为什么单独一个节点: 所有"写库 + 发送"集中在一处,避免业务散落在各节点,
# 也方便以后接多个渠道(QQ/微信/桌面)时只改这里。
#
# 输入 State: output_text(最终回复), goal, decision(含 goal_status)
# 输出 State: 无特殊字段(副作用节点)
# =============================================================================

from __future__ import annotations

from typing import Any, Callable, Coroutine

from ...services import conversations as conversations_service
from ...services import goals as goals_service
from ...services import schedules as schedules_service


async def run(
    state: dict[str, Any],
    sender: Callable[[str, str, str], Coroutine[Any, Any, None]],
) -> dict[str, Any]:
    """收尾: 落库回复、更新目标、发送消息。

    sender 回调签名: async (conversation_id, text, source) -> None
    由 main.py 注入,内部根据会话类型路由到 QQ 桥或桌面端 SSE。
    """
    conversation_id = state.get("conversation_id") or ""

    # ---------------------------------------------------------------- 0. 提取回复
    # reason 节点让 LLM 输出 JSON 决策信封:
    #   {"action": "reply|wait|done|use_tool", "reply_text": "...", "reason": "..."}
    # 这里做两层提取:
    #   1. 找最后一条 AIMessage(工具结果 ToolMessage 不算数);
    #   2. 若内容是 JSON 决策,取 reply_text 字段(去掉信封,只留人话);
    #      若 LLM 直接输出文本(未按 JSON 格式),则原样使用。
    # wait 决策: reply_text 为空,本次不发送任何消息,静默等下一次事件。
    import json

    output_text = ""
    for message in reversed(state.get("messages") or []):
        if not hasattr(message, "type") or getattr(message, "type", "") != "ai":
            continue
        text = str(getattr(message, "content", "") or "").strip()
        if not text:
            continue
        # 尝试解析 JSON 决策信封(失败说明是纯文本,直接使用)
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict) and parsed.get("reply_text"):
                output_text = str(parsed["reply_text"]).strip()
            elif isinstance(parsed, dict) and parsed.get("action") in ("wait", "done"):
                output_text = ""
            else:
                output_text = text
        except (json.JSONDecodeError, TypeError):
            output_text = text
        break
    if not output_text:
        # 没有要回复的内容(例如 agent 决定静默等待对方),直接收尾
        return {}

    # ---------------------------------------------------------------- 1. 落库回复
    # 与 ingest 一致的消息形态;role=assistant 表示这是我们说的话。
    if conversation_id:
        conversations_service.add_message(
            conversation_id,
            {
                "id": None,  # 由 service 自动生成
                "role": "assistant",
                "content": output_text,
                "source": "outbound",
                "created_at": "",  # 由 service 填充当前时间
                "goal_id": "",
            },
        )

    # ---------------------------------------------------------------- 2. 更新目标
    decision = state.get("decision") or {}
    goal_status = decision.get("goal_status") or ""
    goal_progress = decision.get("goal_progress") or ""
    goal = state.get("goal")
    if goal and goal_status:
        goals_service.update_goal_status(
            goal_id=goal.get("id") or "",
            status=goal_status,
            progress=goal_progress,
        )
        # 目标完成/放弃时,取消该会话所有未触发的定时唤醒
        # (例如"转告完成了"就不需要再定时催,避免任务结束后还被打扰)
        if goal_status in ("done", "abandoned") and conversation_id:
            schedules_service.cancel(conversation_id)

    # ---------------------------------------------------------------- 3. 发送回复
    # 只有有内容才发送;wait 决策(output_text 为空)则静默等待下一次事件。
    if output_text and conversation_id:
        await sender(conversation_id, output_text, "reply")

    return {}
