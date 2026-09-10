# =============================================================================
# services/dispatches.py - 外部调度任务服务
# -----------------------------------------------------------------------------
# 职责: dispatches 表的读写。
#
# 什么是"调度任务":
#   其他 agent / 外部系统通过 POST /api/dispatch 派进来的活儿,例如:
#     - handle_message: "这条消息当作某会话收到的,按人设处理"
#     - send_message:   "把这段内容发到某会话"
#     - task:           "围绕这个指令推进"(建 goal,由 agent 自主决定怎么发)
#     - note:           "只记录上下文,不用做事"
#
# 状态机(刻意保持简单):
#   accepted → running → done      正常完成
#                      → failed    执行失败(原因在 error 字段)
#                      → expired   超过 deadline 被拒绝
#   accepted → busy                 会话拥堵,本次未执行(调用方可重试)
#
# 幂等: 调用方给 idempotency_key,重复投递返回既有任务(不重复执行、不重复发送)。
#       这是防"主 agent 重试导致重复发消息"的关键护栏。
# =============================================================================

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from ..db import get_connection

# ---- 任务类型 ----
KIND_HANDLE_MESSAGE = "handle_message"   # 当消息处理(走完整 agent 流程)
KIND_SEND_MESSAGE = "send_message"       # 直接发送(不需要 agent 决策)
KIND_TASK = "task"                       # 建目标并由 agent 推进
KIND_NOTE = "note"                       # 仅记录(不改动任何状态)

# ---- 状态 ----
STATUS_ACCEPTED = "accepted"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
STATUS_EXPIRED = "expired"
STATUS_BUSY = "busy"

ALL_KINDS = {KIND_HANDLE_MESSAGE, KIND_SEND_MESSAGE, KIND_TASK, KIND_NOTE}


def _now() -> str:
    """统一时间格式: UTC ISO 字符串。"""
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# 创建
# ---------------------------------------------------------------------------
def create_dispatch(
    *,
    kind: str,
    caller: str = "",
    conversation_id: str = "",
    payload: dict[str, Any] | None = None,
    idempotency_key: str = "",
    deadline: str = "",
    result_mode: str = "none",
    callback_url: str = "",
) -> dict[str, Any]:
    """创建一条调度任务。

    幂等处理: 若 idempotency_key 已存在,直接返回既有任务
    (状态可能是 done —— 调用方据此判断"这条我已经派过了")。

    返回: 任务的完整字典。
    """
    conn = get_connection()

    # ---- 幂等查重 ----
    if idempotency_key:
        existing = conn.execute(
            "SELECT * FROM dispatches WHERE idempotency_key=?", (idempotency_key,)
        ).fetchone()
        if existing is not None:
            # 注意: 这里也要 _decode —— 否则返回的 payload/result 是 JSON 字符串,
            # 与新建路径(经 get_dispatch 解码)的返回形态不一致。
            record = _decode(dict(existing))
            record["duplicated"] = True  # 告诉调用方"这是重复投递,未重新执行"
            return record

    task_id = uuid.uuid4().hex
    now = _now()
    conn.execute(
        "INSERT INTO dispatches"
        " (task_id, caller, kind, conversation_id, payload, status, result, error,"
        "  idempotency_key, deadline, result_mode, callback_url, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, '', '', ?, ?, ?, ?, ?, ?)",
        (
            task_id,
            caller,
            kind,
            conversation_id,
            json.dumps(payload or {}, ensure_ascii=False),
            STATUS_ACCEPTED,
            idempotency_key,
            deadline,
            result_mode,
            callback_url,
            now,
            now,
        ),
    )
    conn.commit()
    return get_dispatch(task_id) or {}


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------
def get_dispatch(task_id: str) -> dict[str, Any] | None:
    """按任务 ID 查询。"""
    row = get_connection().execute(
        "SELECT * FROM dispatches WHERE task_id=?", (task_id,)
    ).fetchone()
    if row is None:
        return None
    return _decode(dict(row))


def list_dispatches(*, status: str = "", caller: str = "", limit: int = 50) -> list[dict[str, Any]]:
    """列出调度任务(时间倒序),支持按状态/调用方过滤。"""
    sql = "SELECT * FROM dispatches WHERE 1=1"
    params: list[Any] = []
    if status:
        sql += " AND status=?"
        params.append(status)
    if caller:
        sql += " AND caller=?"
        params.append(caller)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    rows = get_connection().execute(sql, params).fetchall()
    return [_decode(dict(r)) for r in rows]


# ---------------------------------------------------------------------------
# 状态更新
# ---------------------------------------------------------------------------
def mark_running(task_id: str) -> None:
    """标记开始执行。"""
    _update(task_id, status=STATUS_RUNNING)


def mark_done(task_id: str, result: dict[str, Any] | None = None) -> None:
    """标记执行成功,并记录结构化产物(如发出的 message_id、回复文本)。"""
    _update(task_id, status=STATUS_DONE, result=result or {})


def mark_failed(task_id: str, error: str) -> None:
    """标记执行失败(错误原因人类可读,便于调用方决定是否重试)。"""
    _update(task_id, status=STATUS_FAILED, error=error)


def mark_expired(task_id: str) -> None:
    """标记已过期(超过 deadline 未执行)。"""
    _update(task_id, status=STATUS_EXPIRED, error="超过 deadline")


def mark_busy(task_id: str) -> None:
    """标记因会话拥堵未执行(调用方可稍后重试)。"""
    _update(task_id, status=STATUS_BUSY, error="目标会话繁忙")


def _update(
    task_id: str,
    *,
    status: str,
    result: dict[str, Any] | None = None,
    error: str = "",
) -> None:
    """更新任务状态(内部统一入口)。"""
    conn = get_connection()
    if result is not None:
        conn.execute(
            "UPDATE dispatches SET status=?, result=?, updated_at=? WHERE task_id=?",
            (status, json.dumps(result, ensure_ascii=False), _now(), task_id),
        )
    elif error:
        conn.execute(
            "UPDATE dispatches SET status=?, error=?, updated_at=? WHERE task_id=?",
            (status, error, _now(), task_id),
        )
    else:
        conn.execute(
            "UPDATE dispatches SET status=?, updated_at=? WHERE task_id=?",
            (status, _now(), task_id),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------
def _decode(record: dict[str, Any]) -> dict[str, Any]:
    """把 payload / result 的 JSON 字符串解成对象,便于 API 直接返回。"""
    for field in ("payload", "result"):
        raw = record.get(field)
        if raw:
            try:
                record[field] = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                pass  # 解不开就保持原样(不因脏数据让查询失败)
    return record


def is_expired(dispatch: dict[str, Any]) -> bool:
    """判断任务是否已超过 deadline。"""
    deadline = dispatch.get("deadline") or ""
    if not deadline:
        return False
    try:
        due = datetime.fromisoformat(deadline)
    except ValueError:
        return False  # 解析不了就当没过期(宽松处理)
    if due.tzinfo is None:
        due = due.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) > due
