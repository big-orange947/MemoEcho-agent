# =============================================================================
# retention.py - 存储保留策略(数据库不无限增长)
# -----------------------------------------------------------------------------
# 为什么需要: 监视中的群聊会把每条消息都写进 messages 表 ——
# 一个活跃群一天几百条,一年就是十几万条;events 审计表同理。
# 不设上界,迟早把磁盘啃满(而且是静默地啃)。
#
# 但"删数据"是危险动作,所以本模块的第一原则是**宁可不删,不可误删**:
#
#   1. 消息只在**已总结进长期记忆之前提下**才可能被删。
#      攒批总结是长期记忆的唯一入口,它读的正是 messages 表 ——
#      若把还没总结的消息删掉,那段内容就永久消失了(记忆和原始记录同时没了)。
#      判定依据: 会话的攒批水位线 memory_batches.last_message_at。
#      没有水位线(从未总结过)⇒ 一条都不删。
#   2. 每个会话始终保留最近 message_keep_min 条(上下文安全垫)。
#   3. 有进行中目标(active goal)的会话跳过 —— 任务上下文不能动。
#   4. 默认天数保守(消息 90 天、审计 30 天),且可配 0 = 永久保留。
#
# 清理范围(按风险从低到高):
#   events          审计日志,只用于排障 —— 过期即删
#   dispatches      外部调度记录,只删已结束的(done/failed/expired/busy)
#   scheduled_events 定时唤醒记录,只删已触发/已取消的
#   report_queue    上报队列,只删已处理完的(acked/dropped/dead)
#   messages        对话消息,受上述全部保护规则约束
#   checkpoints     LangGraph 检查点,只对休眠会话保留最新一份(不是删除会话)
#
# 两种用法:
#   · 定时执行 —— 调度器每 24 小时跑一次 apply()
#   · 手工核对 —— scripts/retention.py --dry-run 先看清楚会删什么
# =============================================================================

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from .config import get_settings
from .db import get_connection


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat()


def _cutoff(now: datetime, days: int) -> str:
    return _iso(now - timedelta(days=max(0, int(days))))


# ---------------------------------------------------------------------------
# 消息(最需要小心的一张表)
# ---------------------------------------------------------------------------
def _message_plan(conn: sqlite3.Connection, now: datetime) -> dict[str, Any]:
    """算出可以安全删除的消息(不执行删除)。

    对每个会话单独判断,返回逐会话的可在删条数与跳过原因 ——
    dry-run 与真正执行共用这段逻辑,保证"看到的"就是"会删的"。
    """
    settings = get_settings()
    days = int(settings.message_retention_days)
    keep_min = max(0, int(settings.message_keep_min))

    result: dict[str, Any] = {
        "candidates": 0,
        "conversations": [],
        "skipped": [],
        "ids": [],
    }
    if days <= 0:
        result["skipped"].append({"reason": "消息保留已关闭(message_retention_days=0)"})
        return result

    cutoff = _cutoff(now, days)

    # 有进行中目标的会话整个跳过: 任务上下文不能动
    active_goals = {
        str(row["conversation_id"])
        for row in conn.execute("SELECT conversation_id FROM goals WHERE status='active'").fetchall()
    }

    rows = conn.execute(
        "SELECT c.id AS id, c.title AS title, c.chat_type AS chat_type, c.external_id AS external_id,"
        "       c.monitor AS monitor, b.last_message_at AS watermark,"
        "       (SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id) AS total,"
        "       (SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id AND m.created_at < ?) AS old"
        " FROM conversations c"
        " LEFT JOIN memory_batches b ON b.conversation_id = c.id",
        (cutoff,),
    ).fetchall()

    for row in rows:
        conversation_id = str(row["id"])
        label = str(row["title"] or row["external_id"] or conversation_id)
        total = int(row["total"] or 0)
        old = int(row["old"] or 0)
        if old <= 0:
            continue

        if conversation_id in active_goals:
            result["skipped"].append({"conversation_id": conversation_id, "label": label, "reason": "有进行中的目标"})
            continue

        # 保护规则: 受监视的会话,水位线之后的消息还没进长期记忆,不能删。
        watermark = str(row["watermark"] or "")
        if int(row["monitor"] or 0) and not watermark:
            result["skipped"].append(
                {"conversation_id": conversation_id, "label": label, "reason": "尚无攒批水位线(从未总结过)"}
            )
            continue

        # 保留最近 keep_min 条(上下文安全垫),其余按"早于 cutoff 且已总结"筛
        conditions = ["conversation_id = ?", "created_at < ?"]
        params: list[Any] = [conversation_id, cutoff]
        if watermark:
            conditions.append("created_at <= ?")
            params.append(watermark)

        floor_ids = {
            str(item["id"])
            for item in conn.execute(
                "SELECT id FROM messages WHERE conversation_id=? ORDER BY created_at DESC LIMIT ?",
                (conversation_id, keep_min),
            ).fetchall()
        }

        victims = [
            str(item["id"])
            for item in conn.execute(
                f"SELECT id FROM messages WHERE {' AND '.join(conditions)}", tuple(params)
            ).fetchall()
            if str(item["id"]) not in floor_ids
        ]
        if not victims:
            continue

        result["ids"].extend(victims)
        result["candidates"] += len(victims)
        result["conversations"].append(
            {
                "conversation_id": conversation_id,
                "label": label,
                "total": total,
                "deletable": len(victims),
                "kept_floor": min(keep_min, total),
                "watermark": watermark[:19],
            }
        )

    return result


# ---------------------------------------------------------------------------
# 其余各表(风险低,规则简单)
# ---------------------------------------------------------------------------
def _events_plan(conn: sqlite3.Connection, now: datetime) -> dict[str, Any]:
    days = int(get_settings().event_retention_days)
    if days <= 0:
        return {"candidates": 0, "ids": [], "note": "已关闭(永久保留)"}
    cutoff = _cutoff(now, days)
    ids = [str(r["id"]) for r in conn.execute("SELECT id FROM events WHERE created_at < ?", (cutoff,)).fetchall()]
    return {"candidates": len(ids), "ids": ids, "cutoff": cutoff[:19]}


def _dispatches_plan(conn: sqlite3.Connection, now: datetime) -> dict[str, Any]:
    days = int(get_settings().dispatch_retention_days)
    if days <= 0:
        return {"candidates": 0, "ids": [], "note": "已关闭(永久保留)"}
    cutoff = _cutoff(now, days)
    # 只删已结束的: accepted/running 是还没跑完的任务,删了就查不到结果
    ids = [
        str(r["task_id"])
        for r in conn.execute(
            "SELECT task_id FROM dispatches WHERE updated_at < ?"
            " AND status IN ('done', 'failed', 'expired', 'busy')",
            (cutoff,),
        ).fetchall()
    ]
    return {"candidates": len(ids), "ids": ids, "cutoff": cutoff[:19]}


def _schedules_plan(conn: sqlite3.Connection, now: datetime) -> dict[str, Any]:
    days = int(get_settings().schedule_retention_days)
    if days <= 0:
        return {"candidates": 0, "ids": [], "note": "已关闭(永久保留)"}
    cutoff = _cutoff(now, days)
    # pending 是还没到点的唤醒,永远不删
    ids = [
        str(r["id"])
        for r in conn.execute(
            "SELECT id FROM scheduled_events WHERE created_at < ? AND status IN ('fired', 'cancelled')",
            (cutoff,),
        ).fetchall()
    ]
    return {"candidates": len(ids), "ids": ids, "cutoff": cutoff[:19]}


def _reports_plan(conn: sqlite3.Connection, now: datetime) -> dict[str, Any]:
    days = int(get_settings().report_retention_days)
    if days <= 0:
        return {"candidates": 0, "ids": [], "note": "已关闭(永久保留)"}
    cutoff = _cutoff(now, days)
    ids = [
        str(r["id"])
        for r in conn.execute(
            "SELECT id FROM report_queue WHERE updated_at < ? AND status IN ('acked', 'dropped', 'dead')",
            (cutoff,),
        ).fetchall()
    ]
    return {"candidates": len(ids), "ids": ids, "cutoff": cutoff[:19]}


# ---------------------------------------------------------------------------
# checkpoint(LangGraph 侧,单独一个库)
# ---------------------------------------------------------------------------
async def _checkpoint_plan(graph: Any, now: datetime) -> dict[str, Any]:
    """找出"休眠会话",它们的历史检查点可以只保留最新一份。

    为什么要做: 每次跑图 LangGraph 都会写新的 checkpoint 行(记录每个超步的状态),
    一个会话聊一年就有成千上万行。图上真正用到的只有**最新一份**(恢复现场),
    中间的只服务于"时光旅行"这种我们没用的能力。

    安全边界:
      · 只处理休眠会话(业务库里 updated_at 早于阈值)—— 正在聊的不动;
      · 正在执行的会话跳过(改它可能打断当前这轮);
      · 策略是 keep_latest —— 保留最新一份,不是删掉会话。
    """
    days = int(get_settings().checkpoint_retention_days)
    plan: dict[str, Any] = {"dormant_threads": 0, "note": ""}
    if days <= 0:
        plan["note"] = "已关闭"
        return plan
    if graph is None or not getattr(graph, "_saver_ready", False):
        # checkpoint 还没初始化(首次运行之前),没有可清理的对象
        plan["note"] = "checkpoint 尚未初始化"
        return plan

    cutoff = _cutoff(now, days)
    busy = set(graph.busy_conversations().keys())
    rows = get_connection().execute(
        "SELECT id FROM conversations WHERE updated_at < ?", (cutoff,)
    ).fetchall()
    plan["dormant_threads"] = len([str(r["id"]) for r in rows if str(r["id"]) not in busy])
    plan["thread_ids"] = [str(r["id"]) for r in rows if str(r["id"]) not in busy]
    return plan


async def _prune_threads(graph: Any, thread_ids: list[str]) -> int:
    """对指定会话执行 checkpoint 精简(只留最新一份)。返回处理条数。"""
    if not thread_ids:
        return 0
    saver = getattr(getattr(graph, "graph", None), "checkpointer", None)
    if saver is None:
        return 0
    await saver.aprune(thread_ids, strategy="keep_latest")
    return len(thread_ids)


# ---------------------------------------------------------------------------
# 对外: 盘点 / 执行
# ---------------------------------------------------------------------------
async def plan(*, now: datetime | None = None, graph: Any = None) -> dict[str, Any]:
    """盘点"如果要清理,会删掉什么"(不执行任何删除)。

    dry-run 与 apply 共用它 —— 保证看到的和删掉的是同一批。
    """
    moment = now or _now()
    conn = get_connection()
    messages = _message_plan(conn, moment)
    result = {
        "generated_at": _iso(moment),
        "enabled": bool(get_settings().retention_enabled),
        "policy": {
            "messages_days": int(get_settings().message_retention_days),
            "messages_keep_min": int(get_settings().message_keep_min),
            "events_days": int(get_settings().event_retention_days),
            "dispatches_days": int(get_settings().dispatch_retention_days),
            "schedules_days": int(get_settings().schedule_retention_days),
            "reports_days": int(get_settings().report_retention_days),
            "checkpoints_days": int(get_settings().checkpoint_retention_days),
        },
        "messages": {
            "candidates": messages["candidates"],
            "conversations": messages["conversations"],
            "skipped": messages["skipped"],
        },
        "events": _events_plan(conn, moment),
        "dispatches": _dispatches_plan(conn, moment),
        "schedules": _schedules_plan(conn, moment),
        "reports": _reports_plan(conn, moment),
        "checkpoints": await _checkpoint_plan(graph, moment),
        "_message_ids": messages["ids"],
    }
    result["total_candidates"] = sum(
        int(result[key]["candidates"])
        for key in ("messages", "events", "dispatches", "schedules", "reports")
    )
    return result


async def apply(*, now: datetime | None = None, graph: Any = None) -> dict[str, Any]:
    """执行清理,返回实际删除条数。

    先盘点再删 —— 复用同一份计划,避免"盘点时算的"和"删除时删的"不一致。
    任何一张表出错都不影响其余表(逐项 try/except): 清理是维护动作,
    不能因为某个细节异常就整个中断。
    """
    if not get_settings().retention_enabled:
        return {"enabled": False, "deleted": {}, "note": "保留策略已关闭"}

    moment = now or _now()
    plan_result = await plan(now=moment, graph=graph)
    conn = get_connection()
    deleted: dict[str, int] = {}

    def _delete(table: str, column: str, ids: list[str]) -> None:
        if not ids:
            deleted[table] = 0
            return
        total = 0
        # 分批删除: 一条 SQL 里塞几千个参数会踩 SQLite 的参数上限,
        # 而且长事务会长时间占着写锁(业务侧表现为"偶尔写不进去")
        for start in range(0, len(ids), 500):
            chunk = ids[start : start + 500]
            placeholders = ",".join("?" * len(chunk))
            cursor = conn.execute(f"DELETE FROM {table} WHERE {column} IN ({placeholders})", tuple(chunk))
            total += cursor.rowcount or 0
        deleted[table] = total

    for table, column, key in (
        ("messages", "id", "_message_ids"),
        ("events", "id", "events"),
        ("dispatches", "task_id", "dispatches"),
        ("scheduled_events", "id", "schedules"),
        ("report_queue", "id", "reports"),
    ):
        ids = plan_result.get(key) if key == "_message_ids" else (plan_result.get(key) or {}).get("ids")
        try:
            _delete(table, column, list(ids or []))
        except Exception as exc:  # noqa: BLE001 - 单表失败不拖累其余表
            print(f"[retention] 清理 {table} 失败: {type(exc).__name__}: {exc}")
            deleted[table] = 0

    conn.commit()

    # checkpoint 精简(异步、另一个库,单独处理)
    pruned = 0
    try:
        thread_ids = list((plan_result.get("checkpoints") or {}).get("thread_ids") or [])
        pruned = await _prune_threads(graph, thread_ids)
    except Exception as exc:  # noqa: BLE001
        print(f"[retention] checkpoint 精简失败: {type(exc).__name__}: {exc}")

    result = {
        "enabled": True,
        "generated_at": plan_result["generated_at"],
        "deleted": deleted,
        "checkpoints_pruned": pruned,
        "skipped_conversations": plan_result["messages"]["skipped"],
        "total_deleted": sum(deleted.values()),
    }
    if result["total_deleted"] or pruned:
        print(
            f"[retention] 清理完成: 消息 {deleted.get('messages', 0)} 条,"
            f"事件 {deleted.get('events', 0)} 条,"
            f"任务 {deleted.get('dispatches', 0)} 条,"
            f"唤醒 {deleted.get('scheduled_events', 0)} 条,"
            f"上报 {deleted.get('report_queue', 0)} 条,"
            f"checkpoint 精简 {pruned} 个会话"
        )
    return result


# ---------------------------------------------------------------------------
# 存储概况(状态页/排障)
# ---------------------------------------------------------------------------
def storage_overview() -> dict[str, Any]:
    """各库文件大小 + 主要表的行数 —— 一眼看出"哪里在涨"。"""
    from .config import get_settings as _settings

    settings = _settings()
    data_dir = settings.data_dir
    files: dict[str, Any] = {}
    for name in ("memo-echo.db", "doppel.sqlite3", "checkpoints.db"):
        path = data_dir / name
        if path.exists():
            size = path.stat().st_size
            files[name] = {"bytes": size, "human": _human(size)}
        else:
            files[name] = {"bytes": 0, "human": "-"}

    counts: dict[str, int] = {}
    conn = get_connection()
    for table in ("conversations", "messages", "events", "goals", "dispatches", "scheduled_events", "report_queue"):
        try:
            row = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
            counts[table] = int(row["n"] or 0)
        except sqlite3.Error:
            counts[table] = -1   # 表不存在(老库/尚未建) —— 不算错误

    total = sum(int(item["bytes"]) for item in files.values())
    return {"files": files, "rows": counts, "total_bytes": total, "total_human": _human(total)}


def _human(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} GB"
