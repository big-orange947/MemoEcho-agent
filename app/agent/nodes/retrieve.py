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
# 输入 State: conversation_id, event, messages(checkpoint 里的对话历史)
# 输出 State: working_memory(含 history / persona / goal_text / memory_block /
#             history_block)
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
    # 用 involving 版本: 目标也可能挂在**别的会话**上,而本会话是任务外联的一环
    # (如"帮问 km"—— 目标在号主那边,km 这边只是被问到的一方)。
    # 不认这种情况,agent 在 km 的会话里就不知道自己为何而来。
    goal = goals_service.get_active_goal_involving(conversation_id)

    # ---------------------------------------------------------------- 4. 长期记忆
    # 检索与"本次输入"相关的长期记忆,格式化成可拼进 prompt 的文本块。
    # 未启用/检索失败都返回空串,不影响主流程 —— 记忆是增强,不是依赖。
    memory_block = await _recall_memory(conversation, state)

    # ---------------------------------------------------------------- 5. 补全上下文
    # 图里的对话历史来自 checkpoint,而 checkpoint 只包含**跑过图**的消息。
    # 这里有一条真实盲区: 会话先被监视(消息只落库、不跑图),之后才开启回复 ——
    # 此时模型看不到监视期记录的任何内容,仿佛"我一直在旁边看着"从未发生。
    # 把数据库里那些**模型尚未见过**的消息补成文本块,交给 reason 拼进提示词。
    history_block = build_history_block(history, state.get("messages") or [])

    # ---------------------------------------------------------------- 6. 工作记忆
    # 把检索结果放进 working_memory(不落库,仅本图执行期间有效)。
    # 注意: 历史消息同时存在于 State.messages(LangGraph 自动管理),
    # 这里再放一份精简版供 reason / reflect / 上下文补全使用。
    working_memory = dict(state.get("working_memory") or {})
    working_memory.update(
        {
            "history": history,                 # list[dict] 消息
            "persona": conversation.get("persona") or "",   # 人设约束
            "goal_text": (goal or {}).get("objective") or "",
            "memory_block": memory_block,       # 长期记忆文本块(可能为空串)
            "history_block": history_block,     # 补全的早前对话(可能为空串)
        }
    )

    return {
        "working_memory": working_memory,
        "goal": goal,  # 让后续节点(state.goal)能直接读到目标
    }


# ---------------------------------------------------------------------------
# 上下文补全(checkpoint 之外的对话记录)
# ---------------------------------------------------------------------------
# 单条消息注入提示词时的截断长度。超长消息(几万字的转发、日志)会吃掉整个
# 上下文预算,截断比"整条塞进去"更安全。
MAX_INJECTED_MESSAGE_CHARS = 500

# LangChain 消息类型 ↔ 数据库 role 的对应关系(仅这两类参与匹配)
_ROLE_BY_MESSAGE_TYPE = {"human": "user", "ai": "assistant"}


def build_history_block(
    history: list[dict[str, Any]],
    messages: list[Any],
) -> str:
    """把数据库里有、但 checkpoint 里没有的消息整理成提示词文本块。

    为什么需要: checkpoint 只记录"跑过图"的消息。会话先被监视(只落库)、
    之后才开启回复时,监视期的内容不在 checkpoint 里 ——
    模型会表现得像从没看过这段对话,而数据库里其实一直存着。

    去重方式(关键,别简化成"内容出现过就跳过"):
      按**顺序匹配**把 checkpoint 里已有的消息从数据库历史中划掉。
      用顺序而不是包含判断,是为了正确处理重复内容 —— agent 前后说过两次
      "好的",第二句不该因为第一句存在就被当成已看过;反过来,顺序里找不到
      匹配的消息,就说明它确实还没进过模型上下文。

    成本兜底: 单条按 MAX_INJECTED_MESSAGE_CHARS 截断(注入条数已由
    list_messages 的 history_max_messages 限制)。

    返回空串 = 没有遗漏的消息(绝大多数会话属于这种情况,不产生额外成本)。
    """
    if not history:
        return ""

    consumed: set[int] = set()
    cursor = 0
    for message in messages:
        role = _ROLE_BY_MESSAGE_TYPE.get(str(getattr(message, "type", "") or ""))
        if role is None:
            continue  # 工具调用链 / 系统消息不参与匹配
        content = str(getattr(message, "content", "") or "").strip()
        if not content:
            continue
        # 从游标处向后找同角色同内容的那条(顺序匹配,已匹配的不再复用)
        for index in range(cursor, len(history)):
            item = history[index]
            if item.get("role") == role and str(item.get("content") or "").strip() == content:
                consumed.add(index)
                cursor = index + 1
                break

    lines: list[str] = []
    for index, item in enumerate(history):
        if index in consumed:
            continue
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        if len(content) > MAX_INJECTED_MESSAGE_CHARS:
            content = content[:MAX_INJECTED_MESSAGE_CHARS] + "…(已截断)"
        who = "我" if item.get("role") == "assistant" else "对方"
        lines.append(f"{who}: {content}")

    if not lines:
        return ""

    return (
        "以下是这段对话**较早时**发生的内容(此前不在你的上下文里,"
        "现在补给你,请把它当作已经发生过的对话):\n" + "\n".join(lines)
    )


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
