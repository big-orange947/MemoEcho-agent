# =============================================================================
# services/goals.py - 目标服务
# -----------------------------------------------------------------------------
# 职责: goals 表的读写。目标(goal)是 v2 的核心概念 ——
# 把"命令"变成"带目标的对话",由 LLM 自主推进,完成与否由 reflect 节点判断。
#
# 对比 v1: 没有 workflow/step/dispatch 状态机,只有一个 active 目标挂在会话上。
# =============================================================================

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from ..db import get_connection


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_goal(conversation_id: str, objective: str) -> dict[str, Any]:
    """在会话上创建一个新目标(active)。同一会话只保留一个 active 目标:
    新建前把旧的 active 目标标记为 abandoned,避免多目标互相干扰。
    """
    conn = get_connection()
    now = _now()

    # 旧目标先放弃(保证"当前目标"唯一)
    conn.execute(
        "UPDATE goals SET status='abandoned', updated_at=? WHERE conversation_id=? AND status='active'",
        (now, conversation_id),
    )

    goal_id = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO goals (id, conversation_id, objective, status, progress, created_at, updated_at)"
        " VALUES (?, ?, ?, 'active', '', ?, ?)",
        (goal_id, conversation_id, objective, now, now),
    )
    conn.commit()
    return {
        "id": goal_id,
        "conversation_id": conversation_id,
        "objective": objective,
        "status": "active",
        "progress": "",
    }


def get_active_goal(conversation_id: str) -> dict[str, Any] | None:
    """读取会话当前 active 目标(纯闲聊时返回 None)。"""
    row = get_connection().execute(
        "SELECT * FROM goals WHERE conversation_id=? AND status='active' ORDER BY created_at DESC LIMIT 1",
        (conversation_id,),
    ).fetchone()
    return dict(row) if row is not None else None


def get_goal(goal_id: str) -> dict[str, Any] | None:
    """按 ID 读取目标(桌面端进度卡用)。"""
    row = get_connection().execute("SELECT * FROM goals WHERE id=?", (goal_id,)).fetchone()
    return dict(row) if row is not None else None


def list_goals(conversation_id: str, limit: int = 20) -> list[dict[str, Any]]:
    """列出会话的目标历史(桌面端展示)。"""
    rows = get_connection().execute(
        "SELECT * FROM goals WHERE conversation_id=? ORDER BY created_at DESC LIMIT ?",
        (conversation_id, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def update_goal_status(goal_id: str, status: str, progress: str = "") -> None:
    """更新目标状态与进度摘要(由 reflect/finalize 节点调用)。

    status 取值: active / done / abandoned
    """
    conn = get_connection()
    now = _now()
    completed_at = now if status == "done" else None
    conn.execute(
        "UPDATE goals SET status=?, progress=?, updated_at=?, completed_at=?"
        " WHERE id=?",
        (status, progress, now, completed_at, goal_id),
    )
    conn.commit()
