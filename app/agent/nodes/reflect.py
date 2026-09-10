# =============================================================================
# nodes/reflect.py - 目标状态评估节点(可选)
# -----------------------------------------------------------------------------
# 职责: 当 LLM 准备直接回复(不再调用工具)时,若会话存在 active 目标,
#       让轻量 LLM 评估该目标是否已达成,产出 status + 一句话进度。
#
# 为什么需要: 桌面端进度卡需要"目标进行到哪了"的可读摘要;
# 完成判定交给 LLM 自主判断(不再有 v1 的 successCriteria 契约)。
# 纯闲聊会话(无 goal)直接跳过,返回空。
#
# 输入 State: goal, working_memory, 最近消息
# 输出 State: decision(含 goal_status / goal_progress)
# =============================================================================

from __future__ import annotations

from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from ..prompts import REFLECT_SYSTEM_PROMPT


def run(state: dict[str, Any], llm: Any) -> dict[str, Any]:
    """评估目标状态。返回 {"decision": {...}} 或空(无目标时)。"""
    goal = state.get("goal")
    if not goal:
        return {}

    # 组装评估上下文: 目标 + 最近几条消息
    working = state.get("working_memory") or {}
    history = (working.get("history") or [])[-6:]  # 最近 6 条足够判断

    transcript = "\n".join(
        f"{'对方' if item.get('role') == 'user' else '我'}: {item.get('content')}"
        for item in history
        if item.get("content")
    )

    system = REFLECT_SYSTEM_PROMPT.format(objective=goal.get("objective") or "")
    user = f"会话记录:\n{transcript or '(空)'}"

    # 调用轻量模型(fast 通道),要求 JSON 输出
    try:
        response = llm.invoke([SystemMessage(content=system), HumanMessage(content=user)])
        text = str(response.content or "").strip()
        # 解析 JSON(容忍首尾花括号外的杂讯)
        import json
        import re

        match = re.search(r"\{.*\}", text, re.DOTALL)
        data = json.loads(match.group(0)) if match else {}
        status = data.get("status", "active")
        progress = data.get("progress", "")[:30]
        if status not in {"active", "done", "abandoned"}:
            status = "active"
    except Exception:  # noqa: BLE001 - 评估失败不影响主流程
        status, progress = "active", ""

    return {
        "decision": {
            **dict(state.get("decision") or {}),
            "goal_status": status,
            "goal_progress": progress,
        }
    }
