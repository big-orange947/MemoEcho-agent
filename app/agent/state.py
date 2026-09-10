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

from langgraph.graph.message import add_messages  # LangGraph 内置的消息追加器


class AgentState(TypedDict, total=False):
    """图状态 schema。所有节点读写这个字典。"""

    # ------------------------------------------------------------ 会话定位
    # 每个会话有独立线程(thread_id),LangGraph 按它存 checkpoint。
    conversation_id: str

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
