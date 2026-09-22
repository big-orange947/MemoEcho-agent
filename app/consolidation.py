# =============================================================================
# consolidation.py - 事实过期与冲突整理(长期记忆的"打理"环节)
# -----------------------------------------------------------------------------
# 要解决的问题(真实场景):
#   · "km 每周三晚上有课" → 后来"课表改到周五了"。旧事实还在库里,
#     检索照样把它捞出来 —— agent 会拿着过期信息去回话;
#   · 两条互相矛盾的说法(自己说"周三有空"、对方说"周三没空")同时存在,
#     谁都不知道该信哪条。
#
# 怎么做: 用 Doppel 的 ConsolidationRunner + DeterministicMemoryConsolidator
# (确定性、**零模型成本**)对每个会话的记忆跑一轮整理:
#   merge    —— 同一槽位上完全相同的重复说法合并;
#   correct  —— 新说法带明确订正标记(聊天里说了"改了/取消了")时,旧说法置为
#               superseded —— 它**不再被检索出来**,过期信息到此为止;
#   conflict —— 说法矛盾又没有明确订正证据时,保留双方 + 写 conflict 标记,
#               并把冲突**报给号主确认**(走既有的上报队列 question 通道)。
#
# 为什么"矛盾"不直接选一个信:
#   选错就是替号主改口供。Doppel 的默认策略也是保守的 —— 没有明确订正证据时
#   宁可标记冲突,也不猜。号主确认之后,下一轮整理会看到新的订正说法并收敛。
#
# 宿主的责任(Doppel 明确把这几件事留给宿主):
#   · 调度: 每轮扫描(见 run_due),一个 scope 一段时间内只整理一次;
#   · 检查点持久化: consolidation checkpoint 存在本项目的 memory_consolidation 表,
#     重启不丢、可重放;
#   · 单写者: 整理只在调度器里跑(单进程),不并发。
# =============================================================================

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from . import memory as memory_layer
from . import reports as reports_service
from .db import get_connection

# 同一个会话两次整理之间的最小间隔(分钟)。
# 整理是本地计算,但没必要每分钟对每个会话都跑一遍 —— 记忆是攒批写入的,
# 变化本来就不频繁。冲突上报更是要克制(见下面 question 的去重键)。
DEFAULT_INTERVAL_MINUTES = 30
# 每轮最多整理几个会话(避免一次调度卡太久;下一轮继续)
DEFAULT_BATCH = 5


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# 检查点(每会话一行)
# ---------------------------------------------------------------------------
def load_checkpoint(scope_key: str) -> dict[str, Any] | None:
    """读取某会话的整理检查点(没有则 None)。"""
    row = get_connection().execute(
        "SELECT checkpoint FROM memory_consolidation WHERE scope_key=?", (scope_key,)
    ).fetchone()
    if row is None or not row["checkpoint"]:
        return None
    try:
        return json.loads(row["checkpoint"])
    except json.JSONDecodeError:
        return None


def save_checkpoint(
    scope_key: str,
    checkpoint: Any,
    *,
    conversation_id: str = "",
    result: dict[str, Any] | None = None,
) -> None:
    """保存整理检查点与最近一次结果(供排障看"整理到底做了什么")。"""
    payload = ""
    if checkpoint is not None:
        payload = json.dumps(
            checkpoint.model_dump(mode="json") if hasattr(checkpoint, "model_dump") else checkpoint,
            ensure_ascii=False,
        )
    conn = get_connection()
    conn.execute(
        "INSERT INTO memory_consolidation (scope_key, conversation_id, checkpoint, last_run_at,"
        " last_result, updated_at) VALUES (?, ?, ?, ?, ?, ?)"
        " ON CONFLICT(scope_key) DO UPDATE SET"
        " conversation_id=excluded.conversation_id, checkpoint=excluded.checkpoint,"
        " last_run_at=excluded.last_run_at, last_result=excluded.last_result,"
        " updated_at=excluded.updated_at",
        (
            scope_key,
            conversation_id,
            payload,
            _now(),
            json.dumps(result or {}, ensure_ascii=False),
            _now(),
        ),
    )
    conn.commit()


def list_state(limit: int = 50) -> list[dict[str, Any]]:
    """查看各会话的整理状态(排障用)。"""
    rows = get_connection().execute(
        "SELECT * FROM memory_consolidation ORDER BY updated_at DESC LIMIT ?", (max(1, limit),)
    ).fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        try:
            item["last_result"] = json.loads(item.get("last_result") or "{}")
        except json.JSONDecodeError:
            item["last_result"] = {}
        item["checkpoint"] = bool(item.get("checkpoint"))
        items.append(item)
    return items


# ---------------------------------------------------------------------------
# 整理一轮
# ---------------------------------------------------------------------------
async def consolidate_conversation(
    conversation: dict[str, Any],
    *,
    notify: bool = True,
) -> dict[str, Any]:
    """整理一个会话的记忆: 合并重复、应用明确订正、标记并上报冲突。

    返回 {"ok", "reason", "operations", "conflicts", "reported"}。
    """
    scope = memory_layer.build_scope(conversation)
    scope_key = str(getattr(scope, "scope_key", "") or "")
    conversation_id = str(conversation.get("id") or "")
    checkpoint = load_checkpoint(scope_key) if scope_key else None

    outcome = await memory_layer.consolidate(conversation, checkpoint=checkpoint)
    if not outcome.get("ok"):
        return {**outcome, "reported": 0}

    if outcome.get("checkpoint") is not None:
        save_checkpoint(
            scope_key,
            outcome["checkpoint"],
            conversation_id=conversation_id,
            result={
                "operations": outcome.get("operations") or {},
                "conflicts": outcome.get("conflicts") or [],
                "errors": outcome.get("errors") or [],
            },
        )

    reported = 0
    if notify:
        for conflict in outcome.get("conflicts") or []:
            if await _report_conflict(conversation, conversation_id, conflict):
                reported += 1
    return {**outcome, "reported": reported}


async def _report_conflict(
    conversation: dict[str, Any],
    conversation_id: str,
    conflict: dict[str, Any],
) -> bool:
    """把一条冲突报进上报队列(question 通道),让号主拍板。

    为什么走队列而不是直接发消息: 与请示(HITL)、重要消息同一条出口 ——
    上游 agent / 前端 / qq 出口都从同一处消费,不用另建通知机制;
    而且离线也不会丢。
    """
    topic = str(conflict.get("topic_key") or "").strip() or "某个话题"
    reason = str(conflict.get("reason") or "").strip()
    memory_ids = [item for item in (conflict.get("memory_ids") or []) if item]

    result = reports_service.enqueue(
        lane=reports_service.LANE_QUESTION,
        conversation_id=conversation_id,
        message_ids=memory_ids,
        payload={
            "summary": f"关于「{topic}」有两条不一致的说法，需要你确认",
            "kind": "memory_conflict",
            "topic_key": topic,
            "detail": reason,
            "memory_ids": memory_ids,
            "sender_name": str(conversation.get("title") or ""),
        },
        # 同一个槽位的同一条冲突只报一次(整理是周期性跑的,不去重会反复打扰)
        dedup_key=f"conflict:{topic}:{','.join(sorted(memory_ids))}",
        status=reports_service.STATUS_PENDING,
    )
    return not result.get("duplicated")


async def run_due(
    *,
    limit: int = DEFAULT_BATCH,
    interval_minutes: int = DEFAULT_INTERVAL_MINUTES,
    now: datetime | None = None,
) -> dict[str, Any]:
    """扫描到期的会话并整理(调度器每轮调用)。

    只整理"开着监视、且已经整理过或写过记忆"的会话:
      · 没写过记忆的会话没什么可整理,直接跳过;
      · 距上次整理不足 interval_minutes 的跳过。

    返回 {"scanned", "consolidated", "conflicts", "skipped"}。
    """
    moment = now or datetime.now(timezone.utc)
    cutoff = (moment - timedelta(minutes=max(1, interval_minutes))).isoformat()

    rows = get_connection().execute(
        "SELECT c.* FROM conversations c"
        " JOIN memory_consolidation m ON m.conversation_id = c.id"
        " WHERE m.last_run_at IS NULL OR m.last_run_at < ?"
        " ORDER BY m.last_run_at ASC LIMIT ?",
        (cutoff, max(1, limit)),
    ).fetchall()

    result = {"scanned": len(rows), "consolidated": 0, "conflicts": 0, "skipped": 0}
    for row in rows:
        conversation = dict(row)
        outcome = await consolidate_conversation(conversation)
        if outcome.get("ok"):
            result["consolidated"] += 1
            result["conflicts"] += len(outcome.get("conflicts") or [])
        else:
            result["skipped"] += 1
            print(f"[consolidation] 跳过: {outcome.get('reason')}")
    return result


def register_scope(conversation_id: str, scope_key: str) -> None:
    """登记一个待整理的会话(攒批写入成功后调用)。

    为什么要"登记"而不是每轮扫全部会话: 没写过记忆的会话没有整理的必要,
    而会话数量可能很多 —— 用一张表把"有记忆的会话"标出来,
    调度时只扫这些(见 run_due 的 JOIN)。
    """
    if not conversation_id or not scope_key:
        return
    conn = get_connection()
    conn.execute(
        "INSERT INTO memory_consolidation (scope_key, conversation_id, checkpoint, last_run_at,"
        " last_result, updated_at) VALUES (?, ?, '', '', '{}', ?)"
        " ON CONFLICT(scope_key) DO UPDATE SET conversation_id=excluded.conversation_id,"
        " updated_at=excluded.updated_at",
        (scope_key, conversation_id, _now()),
    )
    conn.commit()
