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

from ...services import runs as runs_service
from .. import state as state_schema


def _run_id_of(state: dict[str, Any]) -> str:
    """取本次执行的 run_id(控制台发起时才有;其它入口为空串)。

    run_id 放在事件的 context 里透传(Event.context → to_payload → state["event"]),
    这样不必给 run_event / 各节点加参数 —— 埋点是旁路,不该改主流程签名。
    """
    event = state.get("event") or {}
    context = event.get("context") or {}
    return str(context.get("run_id") or "")


def _format_args(tool_args: dict[str, Any]) -> str:
    """把工具参数压成一行可读文本(界面展示用)。"""
    if not tool_args:
        return ""
    parts = []
    for key, value in tool_args.items():
        text = value if isinstance(value, str) else repr(value)
        parts.append(f"{key}={text}")
    return _truncate(" ".join(parts))


def _truncate(text: str, limit: int = 800) -> str:
    """长文本截断: 轨迹是给人扫一眼的,不是完整日志。"""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + f"…(共 {len(text)} 字)"


def _looks_like_failure(result_text: str) -> bool:
    """粗判工具是否失败(只影响轨迹上的成败标记,不参与控制流)。"""
    head = (result_text or "").strip()
    return head.startswith(("错误:", "工具执行出错:", "发送失败:", "未找到称呼"))


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

    # 工具轮次已达软上限 ⇒ 不再执行,回一条提示让模型收尾。
    # 不做这一步的话,ReAct 环在"工具一直失败"或"模型以为没成功"时会无限转
    # (上限常量与路由里的硬上限成对,见 graph.MAX_TOOL_ROUNDS_SOFT)。
    if state_schema.tool_rounds(state) >= state_schema.MAX_TOOL_ROUNDS_SOFT:
        notice = (
            f"错误: 本轮工具调用已达上限({state_schema.MAX_TOOL_ROUNDS_SOFT} 轮),不再执行。"
            "请立刻用一句话回复用户: 做了什么、哪些没做成。"
        )
        return {
            "messages": [
                ToolMessage(
                    content=notice,
                    name=call.get("name") or "",
                    tool_call_id=call.get("id") or "",
                )
                for call in latest.tool_calls
            ]
        }

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
    # 控制台执行轨迹: 只有控制台发起的执行才带 run_id,其它入口(QQ 消息、
    # 定时器)不记轨迹 —— 那些是"值守"过程,看消息流就够了。
    run_id = _run_id_of(state)
    for call in latest.tool_calls:
        tool_name = call.get("name") or ""
        tool_args = call.get("args") or {}
        tool_call_id = call.get("id") or ""

        # 调用前先记一条: 万一进程崩在工具里,"它试图做什么"仍然留痕
        await runs_service.add_step(
            run_id,
            runs_service.STEP_TOOL_CALL,
            name=tool_name,
            detail=_format_args(tool_args),
            conversation_id=conversation_id,
        )

        tool = tools_by_name.get(tool_name)
        success = True
        if tool is None:
            result_text = f"错误: 工具 {tool_name} 不存在"
            success = False
        elif allowed_set is not None and tool_name not in allowed_set:
            # 第二层权限拦截。正常情况下模型看不到未授权的工具(reason 只 bind 授权集),
            # 走到这里意味着模型幻觉、历史消息残留或调用被绕过 —— 必须拒绝并留痕。
            result_text = f"错误: 工具 {tool_name} 在当前会话未授权,已拒绝执行"
            success = False
            _audit_denied(conversation_id, tool_name)
        else:
            try:
                # ainvoke 同时支持同步与异步工具:
                #   异步工具直接 await;同步工具由 LangChain 在线程里执行。
                # config 传给需要上下文的工具(如 wait 取 thread_id)。
                result = await tool.ainvoke(tool_args, tool_config)
                result_text = result if isinstance(result, str) else str(result)
                # 工具约定"失败也用文本回报",所以成败得从返回值判断:
                # send_qq_message 返回"发送失败: …"、resolve_contact 返回"未找到…"。
                # 这里只做展示用的粗判,不参与任何控制流 —— 判错最坏是图标颜色不对。
                success = not _looks_like_failure(result_text)
            except Exception as exc:  # noqa: BLE001 - 工具异常要反馈给模型而不是中断
                result_text = f"工具执行出错: {type(exc).__name__}: {exc}"
                success = False

        await runs_service.add_step(
            run_id,
            runs_service.STEP_TOOL_RESULT,
            name=tool_name,
            detail=_truncate(result_text),
            ok=success,
            conversation_id=conversation_id,
        )

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
