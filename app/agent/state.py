# =============================================================================
# state.py - LangGraph 状态定义
# -----------------------------------------------------------------------------
# LangGraph 的核心概念: State 是流经所有节点的"共享记事本"。
# 每个节点返回一个 dict,框架把返回值和当前 State 合并,再传给下一个节点。
#
# 合并规则: 用 Annotated[类型, reducer] 声明"如何合并"。
#   - 没有 reducer 的字段: 后写的覆盖先写的(直接替换);
#   - 带 reducer 的字段(如 messages): 按 reducer 规则追加而非覆盖。
#
# 注意: 这个 State 是"图执行时的临时状态";持久化到 checkpoint 后,
# 下次同会话事件进来会从 checkpoint 恢复,所以 messages 等必须用 reducer
# 追加,不能每次覆盖(否则历史会丢)。
# =============================================================================

from __future__ import annotations

from typing import Annotated, Any, TypedDict

from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.graph.message import add_messages  # LangGraph 内置的消息追加器

# ---------------------------------------------------------------------------
# ReAct 循环(工具调用轮次)上限
# ---------------------------------------------------------------------------
# 为什么必须有: reason --有 tool_calls--> act --(无条件)--> reason 是一个环。
# 模型陷入"反复调同一个工具"(工具一直失败、或它误以为没成功)时会无限转下去,
# 每轮都要一次 LLM 调用,烧钱且把会话卡死 —— 实测假模型能在一秒内转好几圈。
#   SOFT: 到这里的工具调用不再执行,而是回一条"请直接回复用户"的 ToolMessage,
#         给模型一次体面收尾的机会(它看到提示后通常会写总结);
#   HARD: 模型连提示都无视时,直接掐断循环去 reflect/finalize。
# 放在 state 模块: graph 与 act 节点都要用,而 graph 反过来 import 节点,
# 常量留在这里才能避免循环导入。
MAX_TOOL_ROUNDS_SOFT = 8
MAX_TOOL_ROUNDS_HARD = 10


def tool_rounds(state: dict[str, Any]) -> int:
    """本次执行已经跑了几轮工具(数"最后一条用户消息之后的 ToolMessage")。

    为什么从最后一条 HumanMessage 起算: checkpoint 里存着**历次**执行的
    ToolMessage,直接数会把上一轮的算进来,导致第二轮刚开口就触发上限。
    """
    count = 0
    for message in reversed(state.get("messages") or []):
        if isinstance(message, HumanMessage):
            break
        if isinstance(message, ToolMessage):
            count += 1
    return count


class AgentState(TypedDict, total=False):
    """图状态 schema。所有节点读写这个字典。"""

    # ------------------------------------------------------------ 会话定位
    # 每个会话有独立线程(thread_id),LangGraph 按它存 checkpoint。
    conversation_id: str

    # ------------------------------------------------------------ 幂等短路
    # ingest 判定"这条消息已经处理过"时置为 True;
    # 条件边据此直接从 ingest 走到 END(不推理、不回复)——
    # 避免平台重推导致重复回复(实测出现过两条一样的回复)。
    duplicate: bool

    # ------------------------------------------------------------ 工具权限
    # 本次执行**允许使用**的工具名(由 graph.run_event 按会话策略解析后写入)。
    # reason 只 bind 这些工具,act 拒绝执行集合外的调用 —— 两层用同一份数据。
    # None 表示"未解析"(直接调用/单测),此时不做限制。
    allowed_tools: list[str] | None

    # ------------------------------------------------------------ 请示暂停
    # act 检测到"成功请示了号主"时置为 True。
    # 之后 finalize 不再把本轮的回复发给对方 —— 请示的语义就是"停下等答复",
    # 而工具返回后 ReAct 还会再跑一轮,模型很可能顺手写一句回复发出去。
    # 每次 run_event 都会重置为 False(见 graph._run_graph 的初始 state)。
    awaiting_owner: bool

    # ------------------------------------------------------------ 当前事件
    # 本次触发的事件(可能来自 QQ 或桌面端)。节点用它判断"这次进来的是什么"。
    event: dict[str, Any]

    # ------------------------------------------------------------ 目标(可选)
    # 会话当前目标(如"问km几点上课转告小号")。纯闲聊时为空。
    goal: dict[str, Any] | None

    # ------------------------------------------------------------ 对话历史
    # 消息列表。add_messages 追加合并,并自动按消息 ID 去重。
    # 消息形态: {"id","role","content","source","created_at"}
    messages: Annotated[list[dict[str, Any]], add_messages]

    # ------------------------------------------------------------ 工作记忆
    # 本轮执行中积累的临时信息(如"已确认 km 八点上课"),
    # 由 reason/act 节点读写,不落库(落库的是 messages 和 goals)。
    working_memory: dict[str, Any]

    # ------------------------------------------------------------ 工具结果
    # act 节点执行工具后写入的结果列表(带 reducer 追加,保留多轮工具调用)。
    tool_results: Annotated[list[dict[str, Any]], add_messages]

    # ------------------------------------------------------------ 决策与输出
    # reason 节点输出的结构化决策:
    #   {"action": "reply"|"use_tool"|"wait"|"done",
    #    "tool": "send_qq_message", "tool_args": {...},
    #    "reply_text": "...", "reason": "..."}
    decision: dict[str, Any]

    # finalize 节点的最终回复文本(桌面端/QQ 展示用)
    output_text: str
