# =============================================================================
# nodes/retrieve.py - 上下文检索节点
# -----------------------------------------------------------------------------
# 职责: 在 LLM 思考之前,把"它需要知道的一切"准备好:
#   1. 会话历史(最近 N 条,从 messages 表读);
#   2. 会话信息(人设 persona / 模型绑定);
#   3. 目标 goal(如果有);
#   4. 长期记忆(Doppel 检索,带来源与时间)。
#
# 为什么单独一个节点: 把"检索"和"思考"分离,便于单独调试、
# 加缓存、以及以后替换记忆后端而不影响 reason 节点。
#
# 输入 State: conversation_id, event
# 输出 State: working_memory(含 history / persona / goal_text / memory_block)
# =============================================================================

from __future__ import annotations

from typing import Any

from ...config import get_settings
from ...services import conversations as conversations_service
from ...services import goals as goals_service


async def run(state: dict[str, Any]) -> dict[str, Any]:
    """组装 LLM 决策所需的全部上下文。

    注意: 这是**异步**节点(长期记忆检索是 IO)。
    图在 asyncio 里跑,异步节点可避免"同步等 IO 阻塞整个事件循环"。
    """
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

    # ---------------------------------------------------------------- 4. 长期记忆
    # 检索与"本次输入"相关的长期记忆,格式化成可拼进 prompt 的文本块。
    # 未启用/检索失败都返回空串,不影响主流程 —— 记忆是增强,不是依赖。
    memory_block = await _recall_memory(conversation, state)

    # ---------------------------------------------------------------- 5. 工作记忆
    # 把检索结果放进 working_memory(不落库,仅本图执行期间有效)。
    # 注意: 历史消息同时存在于 State.messages(LangGraph 自动管理),
    # 这里再放一份精简版供 reason 节点直接读取,避免重复从库里取。
    working_memory = dict(state.get("working_memory") or {})
    working_memory.update(
        {
            "history": history,                 # list[dict] 消息
            "persona": conversation.get("persona") or "",   # 人设约束
            "goal_text": (goal or {}).get("objective") or "",
            "memory_block": memory_block,       # 长期记忆文本块(可能为空串)
        }
    )

    return {
        "working_memory": working_memory,
        "goal": goal,  # 让后续节点(state.goal)能直接读到目标
    }


async def _recall_memory(conversation: dict[str, Any], state: dict[str, Any]) -> str:
    """检索长期记忆并格式化成 prompt 文本块。任何失败都返回空串。

    查询词的选择: 优先用"对方刚说的话"(当前事件文本)——
    它与要回应的话题最相关;定时唤醒等无文本事件则回退到最近一条历史消息。
    """
    settings = get_settings()
    if not settings.doppel_enabled or not conversation:
        return ""

    event = state.get("event") or {}
    query = str(event.get("text") or "").strip()
    if not query:
        working = state.get("working_memory") or {}
        for item in reversed(working.get("history") or []):
            if item.get("content"):
                query = str(item["content"])
                break
    if not query:
        return ""

    # 延迟导入: memory 模块会尝试 import doppel_memory,
    # 放在函数内可避免"未安装 Doppel 时整个模块导入失败"的风险。
    from ... import memory

    hits = await memory.recall(conversation, query, limit=settings.memory_recall_limit)
    return memory.format_for_prompt(hits)
