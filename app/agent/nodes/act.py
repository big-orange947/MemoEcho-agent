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

import time
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage


# 为什么是 async 节点 + ainvoke:
#   工具里有异步 IO(如给联系人发消息)。同步节点会被 LangGraph 丢进线程池,
#   线程池里没有事件循环,异步工具根本跑不起来 ——
#   早期版本正是这样导致"发消息"工具永远失败。用 async 节点 + ainvoke 解决。
#   注意: 同步工具也能被 ainvoke 调用(LangChain 会自行处理),改法对所有工具安全。
async def run(state: dict[str, Any], tools_by_name: dict[str, Any]) -> dict[str, Any]:
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

    # 本会话授权的工具集(由 graph.run_event 解析后放进 state)。
    # None = 未解析(直接调用/测试) → 不做限制。
    allowed = state.get("allowed_tools")
    allowed_set = set(allowed) if allowed is not None else None

    # 逐个执行工具调用,收集 ToolMessage
    tool_messages: list[ToolMessage] = []
    # "已请示号主"标记: 请求意味着本轮交涉要暂停(见 escalate_to_owner 的承诺)
    awaiting_owner = False
    for call in latest.tool_calls:
        tool_name = call.get("name") or ""
        tool_args = call.get("args") or {}
        tool_call_id = call.get("id") or ""

        tool = tools_by_name.get(tool_name)
        if tool is None:
            result_text = f"错误: 工具 {tool_name} 不存在"
        elif allowed_set is not None and tool_name not in allowed_set:
            # 第二层权限拦截。正常情况下模型看不到未授权的工具(reason 只 bind 授权集),
            # 走到这里意味着模型幻觉、历史消息残留或调用被绕过 —— 必须拒绝并留痕。
            result_text = f"错误: 工具 {tool_name} 在当前会话未授权,已拒绝执行"
            _audit_denied(conversation_id, tool_name)
        else:
            try:
                # ainvoke 同时支持同步与异步工具:
                #   异步工具直接 await;同步工具由 LangChain 在线程里执行。
                # config 传给需要上下文的工具(如 wait 取 thread_id)。
                result = await tool.ainvoke(tool_args, tool_config)
                result_text = result if isinstance(result, str) else str(result)
            except Exception as exc:  # noqa: BLE001 - 工具异常要反馈给模型而不是中断
                result_text = f"工具执行出错: {type(exc).__name__}: {exc}"

        # 请示成功 ⇒ 标记本轮暂停。
        # 为什么必须在这里判: 工具返回后 ReAct 循环还会继续跑一轮,
        # 模型很可能顺势写一句"我先问问"—— 若不拦,finalize 会把它发给对方,
        # 而请示的语义是"停下等号主"。工具文档里的承诺必须有代码兜底。
        if _is_successful_escalation(tool_name, result_text):
            awaiting_owner = True

        tool_messages.append(
            ToolMessage(content=result_text, name=tool_name, tool_call_id=tool_call_id)
        )

    update: dict[str, Any] = {"messages": tool_messages}
    if awaiting_owner:
        update["awaiting_owner"] = True
    return update


def _is_successful_escalation(tool_name: str, result_text: str) -> bool:
    """这次工具调用是否是一次成功的"请示号主"。

    判定用工具模块导出的常量(而不是在别处重写字符串)——
    提示词、工具返回、暂停判定三处必须一致,否则 HITL 会静默失效。
    """
    if tool_name != "escalate_to_owner":
        return False
    from ...tools.escalate import RESULT_PREFIX

    return RESULT_PREFIX in result_text


def _audit_denied(conversation_id: str, tool_name: str) -> None:
    """记录一次"越权调用被拒"(排障与安全复盘用)。"""
    from ...events import Event, EventKind, EventSource
    from ...services import eventlog

    print(f"[act] 拒绝未授权工具调用: {tool_name} (会话 {conversation_id})")
    eventlog.log_event(
        Event(
            event_id=f"denied-{conversation_id}-{tool_name}-{time.time_ns()}",
            source=EventSource.SYSTEM,
            kind=EventKind.SYSTEM,
            should_respond=False,
            conversation_id=conversation_id,
            text=f"拒绝未授权工具调用: {tool_name}",
        ),
        conversation_id=conversation_id,
    )
