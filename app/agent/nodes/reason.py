# =============================================================================
# nodes/reason.py - LLM 决策节点(代理的"大脑")
# -----------------------------------------------------------------------------
# 职责: 让 LLM 基于检索到的上下文做一次决策。
#
# 实现方式(LangChain 原生 tool-calling):
#   - 把消息历史 + 可用工具 schema 一起发给 LLM;
#   - LLM 可以选择"直接回复"(返回文本),也可以选择"调用某个工具"
#     (返回 tool_calls,含工具名和参数);
#   - 我们不预设步骤 —— LLM 完全自主决定下一步做什么。
#
# 图结构配合:
#   reason --(有 tool_calls)--> act --> reason(循环)
#   reason --(无 tool_calls)--> finalize
# 循环由 graph.py 的条件边控制,这里只负责"产出一个 AIMessage"。
# =============================================================================

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, SystemMessage

from ..prompts import REASON_SYSTEM_PROMPT


def _build_messages(state: dict[str, Any], tools_desc: str) -> list[BaseMessage]:
    """把工作记忆组装成发给 LLM 的消息序列。"""
    working = state.get("working_memory") or {}
    persona = working.get("persona") or ""
    goal_text = working.get("goal_text") or ""

    # 系统提示: 人设 + 目标 + 工具清单
    system = REASON_SYSTEM_PROMPT.format(tools_description=tools_desc)
    if persona:
        system += f"\n\n你在这个会话中的人设: {persona}"
    if goal_text:
        system += f"\n\n当前目标: {goal_text}"
    # 定时唤醒提示: 之前 wait 工具登记的等待到期了,告诉模型为什么继续
    if working.get("wakeup_reason"):
        system += f"\n\n(系统消息: 定时唤醒 —— {working['wakeup_reason']}。请继续推进当前任务。)"

    messages: list[BaseMessage] = [SystemMessage(content=system)]

    # 对话历史用 State.messages(图执行链条),而不是数据库 history:
    # State.messages 里包含本轮 reason→act→reason 循环产生的
    # AIMessage(带 tool_calls)和 ToolMessage(工具结果)——
    # LLM 必须看到它们才能继续决策;只从数据库读会丢掉工具调用上下文,
    # 导致 LLM 反复调用同一个工具(死循环)。
    for msg in state.get("messages") or []:
        if not isinstance(msg, BaseMessage):
            continue
        # 剥离历史 AIMessage 的 reasoning_content(DeepSeek 思考模型产物):
        # 不剥离的话,下次请求序列化时字段丢失,DeepSeek 会报 400。
        # 用 copy 避免改动 checkpoint 中的原始消息。
        if isinstance(msg, AIMessage) and msg.additional_kwargs:
            msg = msg.model_copy(deep=True)
            msg.additional_kwargs.pop("reasoning_content", None)
        messages.append(msg)

    return messages


def run(state: dict[str, Any], llm: Any, tools: list[Any]) -> dict[str, Any]:
    """执行一次 LLM 决策,返回带 tool_calls 的 AIMessage。

    参数:
      llm:  已 bind_tools 的 LangChain chat model
      tools: 可用工具列表(用于在工具调用后把结果喂回模型)
    """
    # 工具描述: 直接交给 LLM 的是 bind 后的工具;这里仅为提示词展示
    tools_desc = "\n".join(f"- {t.name}: {t.description}" for t in tools)

    messages = _build_messages(state, tools_desc)

    # 调用模型。注意: llm 已经 bind_tools,所以 LLM 可以返回 tool_calls。
    response: BaseMessage = llm.invoke(messages)

    # 剥离 DeepSeek 思考模型的 reasoning_content(additional_kwargs 里):
    # 该字段若不剥离,会随 AIMessage 存进 checkpoint;下次会话恢复后再发
    # 给 API 时,DeepSeek 要求"原样回传 reasoning_content",而 langchain
    # 序列化会丢字段,导致 400 报错(实测踩过)。
    if isinstance(response, AIMessage) and response.additional_kwargs:
        response.additional_kwargs.pop("reasoning_content", None)

    # 把 AIMessage 追加到 State.messages(LangGraph 自动管理历史,
    # 下一轮 reason 循环时,这个 AIMessage 会作为上下文继续传给模型)
    return {"messages": [response]}
