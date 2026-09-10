# =============================================================================
# nodes/retrieve.py - 上下文检索节点
# -----------------------------------------------------------------------------
# 职责: 在 LLM 思考之前,把"它需要知道的一切"准备好:
#   1. 会话历史(最近 N 条,从 messages 表读);
#   2. 会话信息(人设 persona / 模型绑定);
#   3. 目标 goal(如果有);
#   4. 长期记忆(预留接口,首版可为空)。
#
# 为什么单独一个节点: 把"检索"和"思考"分离,便于单独调试、
# 加缓存、以及以后接入向量检索/图谱记忆而不影响 reason 节点。
#
# 输入 State: conversation_id
# 输出 State: working_memory(包含 history / persona / goal_text 等)
# =============================================================================

from __future__ import annotations

from typing import Any

from ...config import get_settings
from ...services import conversations as conversations_service
from ...services import goals as goals_service


def run(state: dict[str, Any]) -> dict[str, Any]:
    """组装 LLM 决策所需的全部上下文。"""
    conversation_id = state.get("conversation_id") or ""

    # ---------------------------------------------------------------- 1. 会话信息
    conversation = conversations_service.get_conversation(conversation_id) or {}

    # ---------------------------------------------------------------- 2. 历史消息
    # 只取最近 N 条,控制 token 成本;按时间正序返回(旧→新),LLM 更好理解。
    limit = get_settings().history_max_messages
    history = conversations_service.list_messages(conversation_id, limit=limit)

    # ---------------------------------------------------------------- 3. 当前目标
    # 取会话上最近一个 active 目标(没有则为 None,即纯闲聊)。
    goal = goals_service.get_active_goal(conversation_id)

    # ---------------------------------------------------------------- 4. 工作记忆
    # 把检索结果放进 working_memory(不落库,仅本图执行期间有效)。
    # 注意: 历史消息同时存在于 State.messages(LangGraph 自动管理),
    # 这里再放一份精简版供 reason 节点直接读取,避免重复从库里取。
    working_memory = dict(state.get("working_memory") or {})
    working_memory.update(
        {
            "history": history,                 # list[dict] 消息
            "persona": conversation.get("persona") or "",   # 人设约束
            "goal_text": (goal or {}).get("objective") or "",
        }
    )

    return {
        "working_memory": working_memory,
        "goal": goal,  # 让后续节点(state.goal)能直接读到目标
    }
