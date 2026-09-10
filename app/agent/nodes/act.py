# =============================================================================
# nodes/act.py - 工具执行节点
# -----------------------------------------------------------------------------
# 职责: 执行 reason 节点返回的 tool_calls,把结果作为 ToolMessage 回写,
#       让 LLM 在下一轮 reason 中"看到结果"继续决策(ReAct 循环)。
#
# 关键点(LangGraph 标准模式):
#   - ToolMessage.name 必须与工具名一致;
#   - ToolMessage.tool_call_id 必须与 AIMessage 里的 tool_call.id 一致,
#     框架/模型靠这个 ID 把"调用"和"结果"配对;
#   - 工具执行失败也返回错误文本(而不是抛异常),让 LLM 自己决定怎么办。
# =============================================================================

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, ToolMessage


def run(state: dict[str, Any], tools_by_name: dict[str, Any]) -> dict[str, Any]:
    """执行状态中最新 AIMessage 的所有工具调用。"""
    # 取最近一条 AIMessage;如果它没有 tool_calls,说明是纯回复,无需执行。
    latest = None
    for message in reversed(state.get("messages") or []):
        if isinstance(message, AIMessage):
            latest = message
            break
    if latest is None or not getattr(latest, "tool_calls", None):
        return {}

    # 工具调用需要知道"当前会话是谁"(wait 工具靠 thread_id 登记唤醒)
    conversation_id = state.get("conversation_id") or ""
    tool_config = {"configurable": {"thread_id": conversation_id}}

    # 逐个执行工具调用,收集 ToolMessage
    tool_messages: list[ToolMessage] = []
    for call in latest.tool_calls:
        tool_name = call.get("name") or ""
        tool_args = call.get("args") or {}
        tool_call_id = call.get("id") or ""

        tool = tools_by_name.get(tool_name)
        if tool is None:
            result_text = f"错误: 工具 {tool_name} 不存在"
        else:
            try:
                # 调用 LangChain 工具(同步),并把会话 config 传进去,
                # 让需要上下文(如 thread_id)的工具能拿到。结果可能是
                # 字符串,也可能是结构化对象。
                result = tool.invoke(tool_args, tool_config)
                result_text = result if isinstance(result, str) else str(result)
            except Exception as exc:  # noqa: BLE001 - 工具异常要反馈给模型而不是中断
                result_text = f"工具执行出错: {type(exc).__name__}: {exc}"

        tool_messages.append(
            ToolMessage(content=result_text, name=tool_name, tool_call_id=tool_call_id)
        )

    return {"messages": tool_messages}
