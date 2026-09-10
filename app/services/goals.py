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


# ---------------------------------------------------------------------------
# 目标涉及的会话(跨会话任务)
# ---------------------------------------------------------------------------
def link_conversation(goal_id: str, conversation_id: str) -> None:
    """把某个会话登记为"该目标牵涉到的会话"。

    为什么需要: 一次任务常常横跨多个会话 ——
      号主说"帮我问 km 今晚几点上课,然后转告小号":
      目标挂在**下指令的会话**上,但 agent 会主动去联系 km。
      km 的回复落在 km 的会话里;若不登记,那条回复既没有目标撑腰、
      该会话也没开自动回复,就会被直接丢弃,任务永远卡在"等回话"。

    幂等: 同一 (goal, conversation) 只登记一次。
    """
    if not goal_id or not conversation_id:
        return
    conn = get_connection()
    conn.execute(
        "INSERT OR IGNORE INTO goal_conversations (goal_id, conversation_id, created_at)"
        " VALUES (?, ?, ?)",
        (goal_id, conversation_id, _now()),
    )
    conn.commit()


def list_goal_conversations(goal_id: str) -> list[str]:
    """列出某个目标牵涉的所有会话 ID。"""
    rows = get_connection().execute(
        "SELECT conversation_id FROM goal_conversations WHERE goal_id=?",
        (goal_id,),
    ).fetchall()
    return [str(row["conversation_id"]) for row in rows]


def get_active_goal_involving(conversation_id: str) -> dict[str, Any] | None:
    """读取"牵涉到该会话"的 active 目标(自己挂的,或任务外联涉及到的)。

    与 get_active_goal 的区别: 后者只看目标是否**挂在本会话**;
    本函数还会认领"agent 为了完成某个目标而主动联系了本会话"的情况 ——
    对方的回复因此能够唤醒任务继续推进(配合 policy.decide 的任务授权判定)。
    """
    row = get_connection().execute(
        "SELECT g.* FROM goals g"
        " LEFT JOIN goal_conversations gc ON gc.goal_id = g.id"
        " WHERE g.status='active' AND (g.conversation_id = ? OR gc.conversation_id = ?)"
        " ORDER BY g.created_at DESC LIMIT 1",
        (conversation_id, conversation_id),
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

    结项时顺带清理跨会话登记: 目标结束了,它牵涉的会话就不该再被
    "任务授权"放行(否则那些会话会一直保持可对话状态,越过值守策略)。
    """
    conn = get_connection()
    now = _now()
    completed_at = now if status == "done" else None
    conn.execute(
        "UPDATE goals SET status=?, progress=?, updated_at=?, completed_at=?"
        " WHERE id=?",
        (status, progress, now, completed_at, goal_id),
    )
    if status in ("done", "abandoned") and goal_id:
        conn.execute("DELETE FROM goal_conversations WHERE goal_id=?", (goal_id,))
    conn.commit()
