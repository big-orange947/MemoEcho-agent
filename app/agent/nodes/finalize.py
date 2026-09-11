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
# 长期记忆: 本节点**不写**。机器人自己的话同样"攒批后才总结"(否则
# 一句"好的"就是一条记忆),统一由 app/batches.py 按窗口总结写入。
#
# 输入 State: output_text(最终回复), goal, decision(含 goal_status)
# 输出 State: 无特殊字段(副作用节点)
# =============================================================================

from __future__ import annotations

from typing import Any, Callable, Coroutine

from langgraph.graph.message import RemoveMessage

from ...config import get_settings
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
        # 没有要回复的内容(例如 agent 决定静默等待对方): 不发消息,但照样裁剪历史
        return _trim_update(state)

    # ---------------------------------------------------------------- 0b. 请示暂停
    # "已请示号主"意味着本轮停下等答复 —— 此时**不能**把模型的回复发给对方。
    # 为什么需要这道闸: 工具返回后 ReAct 还会再跑一轮,模型常常顺势写一句
    # "好,我先问一下";若不拦,它就发出去了 —— 而请示的语义是暂停。
    # 工具对模型的承诺(本轮不回复对方)必须有代码兜底,不能只靠提示词。
    if state.get("awaiting_owner"):
        print(f"[finalize] 已请示号主,本轮不回复对方(会话 {conversation_id})")
        return _trim_update(state)

    # ---------------------------------------------------------------- 1. 落库回复
    # 与 ingest 一致的消息形态;role=assistant 表示这是我们说的话。
    message_id = ""
    if conversation_id:
        message_id = conversations_service.add_message(
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
        outcome = await sender(conversation_id, output_text, "reply")
        # 回填平台消息 ID: 平台会把这条消息回显一份,有了 ID 才能认出是"自己发的"、
        # 不重复入库(见 app/recorder.py 的回显去重)
        platform_id = str((outcome or {}).get("platform_message_id") or "") if isinstance(outcome, dict) else ""
        if platform_id and message_id:
            conversations_service.set_platform_message_id(message_id, platform_id)

    # ---------------------------------------------------------------- 4. 裁剪历史
    return _trim_update(state)


# ---------------------------------------------------------------------------
# 历史裁剪(checkpoint 有界,见函数注释)
# ---------------------------------------------------------------------------
def _message_id(message: Any) -> str:
    """取消息 ID(兼容 LangChain 消息对象与普通 dict 两种形态)。"""
    if isinstance(message, dict):
        return str(message.get("id") or "")
    return str(getattr(message, "id", "") or "")


def _is_tool_message(message: Any) -> bool:
    if isinstance(message, dict):
        return str(message.get("role") or "") == "tool"
    return str(getattr(message, "type", "") or "") == "tool"


def _trim_update(state: dict[str, Any]) -> dict[str, Any]:
    """把窗口外的历史消息从 checkpoint 里删掉,返回 state 更新(可能为空)。

    为什么必须做: reason 节点会把 State.messages **全量**拼进提示词,
    而 checkpoint 是只增不减的 —— 会话越长每轮 token 越多。
    一旦开启监视(会话永久增长),这条成本曲线会失控,所以在这里封顶。

    安全性(不能切坏配对): 删除区间如果以 ToolMessage 开头,说明它的发起方
    (那条带 tool_calls 的 AIMessage)被删掉了,模型会收到"孤儿工具结果"而报错。
    因此把起点往前退到非 ToolMessage 的位置,保证保留区间自包含。

    注意: 只动 checkpoint(模型的短期工作区);数据库 messages 表一条不少 ——
    retrieve 仍按配置条数读取历史,审计与前端翻旧账都不受影响。
    """
    window = max(1, int(get_settings().history_max_messages))
    messages = list(state.get("messages") or [])
    if len(messages) <= window:
        return {}

    keep_from = len(messages) - window
    while keep_from > 0 and _is_tool_message(messages[keep_from]):
        keep_from -= 1

    removals = [
        RemoveMessage(id=message_id)
        for message_id in (_message_id(m) for m in messages[:keep_from])
        if message_id
    ]
    return {"messages": removals} if removals else {}
