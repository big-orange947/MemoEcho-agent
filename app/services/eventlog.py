# =============================================================================
# services/eventlog.py - 事件审计日志服务
# -----------------------------------------------------------------------------
# 职责: events 表的读写。
#
# 为什么需要审计:
#   1. 排障 —— "那条消息到底收到没有?解析成什么了?" —— 直接查表,不用翻日志文件;
#   2. 复盘 —— 撤回通知、好友申请这类"不需要回应"的事件也留痕;
#   3. 幂等排查 —— 同一 event_id 出现多次即说明平台重推,可据此验证去重逻辑。
#
# 与 messages 表的区别:
#   messages  = 对话内容(agent 的上下文来源,参与 LLM 推理)
#   events    = 事件流水(全量留痕,不参与推理,仅供人查)
#   因此"仅记录"的事件(通知/请求/自发回显)只进 events,不进 messages。
# =============================================================================

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from ..db import get_connection
from ..events import Event


def _now() -> str:
    """统一时间格式: UTC ISO 字符串。"""
    return datetime.now(timezone.utc).isoformat()


def log_event(event: Event, *, conversation_id: str = "") -> None:
    """记录一条事件到审计表(幂等: 同 ID 重复记录会被忽略)。

    参数:
      event:           要记录的事件
      conversation_id: 关联会话 ID(调用方若已知则传入,便于按会话检索)

    注意: 使用 INSERT OR IGNORE —— 同一事件(相同 event_id)重复进入时
    只保留第一条,这样"重复推送"本身不会污染审计表。
    """
    conn = get_connection()
    try:
        payload = json.dumps(event.raw, ensure_ascii=False) if event.raw else ""
    except (TypeError, ValueError):
        # raw 里可能有不可序列化对象: 退化为字符串,保证审计不失败
        payload = str(event.raw)

    conn.execute(
        "INSERT OR IGNORE INTO events"
        " (id, event_type, source, should_respond, conversation_id, summary, payload, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            event.event_id,
            event.kind,
            event.source,
            1 if event.should_respond else 0,
            conversation_id or event.conversation_id or "",
            _summarize(event),
            payload,
            _now(),
        ),
    )
    conn.commit()


def _summarize(event: Event) -> str:
    """生成一句话描述(events 表里直接可读,不用解析 payload)。"""
    text = (event.text or "").strip()
    if len(text) > 120:
        text = text[:117] + "..."

    # 通知/请求类事件的 text 本身就是描述(由 onebot 解析器生成)
    if event.kind in ("notice", "request"):
        return text or event.kind

    if event.kind == "timer":
        return f"[定时唤醒] {text}"

    if event.kind == "instruction":
        return f"[调度指令] {text}"

    if event.kind == "message_sent":
        return f"[自发消息] {text}"

    if event.kind == "message":
        who = event.sender_name or event.sender_id or "?"
        where = "群" if event.chat_type == "group" else "私聊"
        return f"[{where}] {who}: {text}"

    return text or event.kind


def list_events(
    conversation_id: str = "",
    *,
    limit: int = 100,
    kind: str = "",
) -> list[dict[str, Any]]:
    """查询审计事件(按时间倒序: 最新的在前)。

    参数:
      conversation_id: 只看某个会话(空=全部)
      limit:           返回条数上限
      kind:            只看某类事件(空=全部)
    """
    sql = "SELECT * FROM events WHERE 1=1"
    params: list[Any] = []
    if conversation_id:
        sql += " AND conversation_id = ?"
        params.append(conversation_id)
    if kind:
        sql += " AND event_type = ?"
        params.append(kind)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    rows = get_connection().execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def count_by_kind() -> dict[str, int]:
    """按事件类别统计条数(状态页/排障时快速看系统都在处理什么)。"""
    rows = get_connection().execute(
        "SELECT event_type, COUNT(*) AS n FROM events GROUP BY event_type ORDER BY n DESC"
    ).fetchall()
    return {row["event_type"]: row["n"] for row in rows}
