# =============================================================================
# services/schedules.py - 定时唤醒服务
# -----------------------------------------------------------------------------
# 职责: scheduled_events 表的读写。
# 使用方:
#   - tools/wait.py       LLM 调 wait 工具时登记一条"到期唤醒";
#   - app/scheduler.py    后台任务每 1 秒查一次到期记录,发 timer 事件。
#
# 设计要点:
#   - 到期后标记 fired 而非删除: 保留痕迹,方便排查"这个唤醒是否触发过";
#   - 所有查询只扫 pending 记录,避免历史记录拖慢轮询。
# =============================================================================

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from ..db import get_connection


def _now() -> str:
    """统一时间格式: UTC ISO 字符串(与 conversations 服务一致)。"""
    return datetime.now(timezone.utc).isoformat()


def create_schedule(conversation_id: str, due_at: str, note: str = "") -> str:
    """登记一条定时唤醒,返回记录 ID。

    due_at: 到期时间(UTC ISO 字符串),通常由 wait 工具按"now + 秒数"计算。
    """
    schedule_id = uuid.uuid4().hex
    conn = get_connection()
    conn.execute(
        "INSERT INTO scheduled_events (id, conversation_id, due_at, note, status, created_at)"
        " VALUES (?, ?, ?, ?, 'pending', ?)",
        (schedule_id, conversation_id, due_at, note, _now()),
    )
    conn.commit()
    return schedule_id


def list_due(now: str, limit: int = 0) -> list[dict[str, Any]]:
    """返回所有已到期且未触发的唤醒记录(按到期时间正序)。

    limit: 最多返回多少条(0=不限)。调用方用它控制单轮处理量,
    防止大量积压一次性涌入事件总线(见 scheduler._tick)。
    """
    sql = "SELECT * FROM scheduled_events WHERE status='pending' AND due_at <= ?" \
          " ORDER BY due_at ASC"
    if limit > 0:
        sql += " LIMIT ?"
        rows = get_connection().execute(sql, (now, limit)).fetchall()
    else:
        rows = get_connection().execute(sql, (now,)).fetchall()
    return [dict(r) for r in rows]


def mark_fired(schedule_id: str) -> None:
    """把记录标记为已触发(防止重复发送 timer 事件)。"""
    conn = get_connection()
    conn.execute(
        "UPDATE scheduled_events SET status='fired' WHERE id=? AND status='pending'",
        (schedule_id,),
    )
    conn.commit()


def cancel(conversation_id: str) -> int:
    """取消某会话所有未触发的唤醒(例如目标已完成时,不必要再催)。返回取消条数。"""
    conn = get_connection()
    cur = conn.execute(
        "UPDATE scheduled_events SET status='cancelled'"
        " WHERE conversation_id=? AND status='pending'",
        (conversation_id,),
    )
    conn.commit()
    return cur.rowcount