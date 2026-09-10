# =============================================================================
# reports.py - 重要消息上报(规则初筛 → 快模型复核 → 入队)
# -----------------------------------------------------------------------------
# 需求来源: "监视还要做到有重要消息自主汇报"; "后台需要上报的消息是要存放在
# 一个类似消息中间件上,也许上游的 agent 看到之后会自动上报"。
#
# 因此本模块的职责边界很明确: **发现 + 排队**,不负责"报给用户"。
# 最终要不要通知人、用什么口吻通知,由消费队列的上游 agent 决定。
#
# 流水线(三级,逐级加成本,默认最省):
#   ① 规则初筛(零模型成本): @号主/机器人、自定义关键词、疑问请求语气、白名单联系人
#      —— 命中才产生"候选";
#   ② 快模型复核(每条候选 1 次 fast 调用,可注入/可关闭): 判断这是不是真的值得打扰人,
#      并写一句话摘要。批量化(一批一次调用) + 每日配额封顶;
#   ③ 入队: urgent 立即 pending(上游可取); 其余按 lane 归入摘要批。
#
# 配额超限怎么办: **退化为纯规则**(候选直接按规则结论入队)。
# 宁可多报几条,也不能因为省钱把"急事"漏掉 —— 漏报的代价远大于多打扰一次。
# =============================================================================

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Iterable

from .config import get_settings
from .db import get_connection
from .services import configs as configs_service

# ---------------------------------------------------------------------------
# lane(通道)与状态
# ---------------------------------------------------------------------------
LANE_URGENT = "urgent"      # 立即上报(上游应尽快取走)
LANE_NORMAL = "normal"      # 普通候选(可进摘要批)
LANE_QUESTION = "question"  # HITL 请示: agent 拿不准,回来问号主
LANE_DIGEST = "digest"      # 批量汇总(一段时间的 normal 合并成一条)

# 队列状态。candidate = 已过规则、等快模型复核(比 pending 早一步)。
STATUS_CANDIDATE = "candidate"
STATUS_PENDING = "pending"
STATUS_CLAIMED = "claimed"
STATUS_ACKED = "acked"
STATUS_DROPPED = "dropped"
STATUS_DEAD = "dead"

# 认领失败多少次后进死信(避免一条坏消息被无限重投)
MAX_ATTEMPTS = 5
# 默认租约时长(秒): 上游认领后必须在此时间内 ack,否则视为没处理完,可被重新认领
DEFAULT_LEASE_SECONDS = 120

# 快模型复核: 一次调用处理多少条候选 / 候选攒多久就必须复核
REVIEW_BATCH_SIZE = 5
REVIEW_MAX_AGE_SECONDS = 120

# ---------------------------------------------------------------------------
# 规则初筛(零模型成本)
# ---------------------------------------------------------------------------
# 疑问/请求语气。用保守的短词表而不是复杂正则 —— 宁可多报(复核会兜住),
# 也不要因为规则太严漏掉"你能来一下吗"这类真正需要人回应的消息。
_REQUEST_PATTERNS = (
    "吗", "呢", "?", "？", "几点", "什么时候", "怎么", "为什么", "哪",
    "帮", "请", "麻烦", "能不能", "可不可以", "可以吗", "行不行", "有空",
    "急", "尽快", "马上", "立刻", "截止", "ddl",
)


def evaluate_rules(
    conversation: dict[str, Any],
    *,
    text: str,
    sender_id: str = "",
    sender_name: str = "",
    chat_type: str = "",
) -> list[str]:
    """对一条消息跑规则初筛,返回命中的规则名列表(空 = 不候选)。

    零模型成本: 纯字符串判断,可以对所有监视中的消息全量跑。
    """
    reasons: list[str] = []
    content = text or ""

    # ---- 规则 1: @了号主/机器人 ----
    # 群里被点名是最强的"需要你看一眼"信号。
    bot_qq = get_settings().bot_qq
    if bot_qq and f"@{bot_qq}" in content:
        reasons.append("at_bot")

    # ---- 规则 2: 自定义关键词(会话级) ----
    keywords = conversation.get("alert_keywords") or []
    if isinstance(keywords, str):
        try:
            keywords = json.loads(keywords) if keywords.startswith("[") else [k.strip() for k in keywords.split(",")]
        except json.JSONDecodeError:
            keywords = [k.strip() for k in keywords.split(",")]
    for keyword in keywords or []:
        keyword = str(keyword).strip()
        if keyword and keyword in content:
            reasons.append(f"keyword:{keyword}")

    # ---- 规则 3: 疑问 / 请求语气 ----
    lowered = content.lower()
    for pattern in _REQUEST_PATTERNS:
        if pattern in lowered:
            reasons.append(f"pattern:{pattern}")
            break   # 只记一个代表,避免 reason 爆炸

    # ---- 规则 4: 白名单联系人 ----
    # 有些人说的话天然重要(家人、老板、常联系的人),他们的消息一律候选。
    raw = configs_service.get_config("alert_contacts", "")
    watchlist = _parse_contacts(raw)
    if watchlist and str(sender_id) in watchlist:
        reasons.append("watchlist")

    return reasons


def _parse_contacts(raw: str) -> set[str]:
    """解析白名单联系人: 兼容 JSON 数组与逗号/换行分隔的写法。"""
    text = (raw or "").strip()
    if not text:
        return set()
    if text.startswith("["):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return {str(item).strip() for item in parsed if str(item).strip()}
        except json.JSONDecodeError:
            pass
    return {part.strip() for part in re.split(r"[,\n]", text) if part.strip()}


# ---------------------------------------------------------------------------
# 入队(幂等)
# ---------------------------------------------------------------------------
def enqueue(
    *,
    lane: str = LANE_NORMAL,
    conversation_id: str = "",
    message_ids: Iterable[str] = (),
    payload: dict[str, Any] | None = None,
    dedup_key: str = "",
    status: str = STATUS_PENDING,
) -> dict[str, Any]:
    """把一条上报候选写进队列(幂等)。

    幂等键: (conversation_id, dedup_key)。同一条消息被重复处理(平台重推、
    服务重启后重跑)时不会重复入队 —— 否则上游会被同一件事反复打扰。

    返回 {"id": str, "duplicated": bool}。
    """
    conn = get_connection()
    now = _now()
    ids = ",".join(str(item) for item in message_ids if item)

    if dedup_key:
        existing = conn.execute(
            "SELECT id FROM report_queue WHERE conversation_id=? AND dedup_key=?",
            (conversation_id, dedup_key),
        ).fetchone()
        if existing is not None:
            return {"id": existing["id"], "duplicated": True}

    record_id = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO report_queue"
        " (id, lane, conversation_id, message_ids, payload, dedup_key, status,"
        "  attempts, claimed_by, lease_expires_at, created_at, updated_at, acked_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, 0, '', '', ?, ?, '')",
        (
            record_id,
            lane,
            conversation_id,
            ids,
            json.dumps(payload or {}, ensure_ascii=False),
            dedup_key,
            status,
            now,
            now,
        ),
    )
    conn.commit()
    return {"id": record_id, "duplicated": False}


# ---------------------------------------------------------------------------
# 消费(claim / ack / drop)
# ---------------------------------------------------------------------------
def claim(
    *,
    limit: int = 10,
    lane: str = "",
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    claimed_by: str = "upstream",
) -> list[dict[str, Any]]:
    """认领待处理记录(至少一次投递语义)。

    流程: 先把过期租约的认领放回 pending(见 reap_expired),
    再原子地取一批 pending → 标记 claimed 并写租约到期时间。

    上游拿到后必须 ack(处理完了)或 drop(决定不报);
    若中途挂了,租约到期后这条会重新变成 pending 供别人取走。
    """
    reap_expired()
    conn = get_connection()
    now = _now()
    lease_until = (datetime.now(timezone.utc) + timedelta(seconds=max(1, lease_seconds))).isoformat()

    sql = "SELECT * FROM report_queue WHERE status=?"
    params: list[Any] = [STATUS_PENDING]
    if lane:
        sql += " AND lane=?"
        params.append(lane)
    sql += " ORDER BY created_at ASC LIMIT ?"
    params.append(max(1, limit))

    rows = conn.execute(sql, params).fetchall()
    claimed: list[dict[str, Any]] = []
    for row in rows:
        attempts = int(row["attempts"] or 0) + 1
        conn.execute(
            "UPDATE report_queue SET status=?, attempts=?, claimed_by=?,"
            " lease_expires_at=?, updated_at=? WHERE id=?",
            (STATUS_CLAIMED, attempts, claimed_by, lease_until, now, row["id"]),
        )
        item = dict(row)
        item.update(
            {
                "status": STATUS_CLAIMED,
                "attempts": attempts,
                "claimed_by": claimed_by,
                "lease_expires_at": lease_until,
            }
        )
        claimed.append(_decode(item))
    conn.commit()
    return claimed


def ack(record_id: str, *, claimed_by: str = "") -> bool:
    """确认处理完成(上游已经决定并完成了对外通知)。"""
    return _finish(record_id, STATUS_ACKED, claimed_by=claimed_by)


def drop(record_id: str, *, claimed_by: str = "", reason: str = "") -> bool:
    """放弃这条上报(上游判断不重要,不值得打扰人)。"""
    return _finish(record_id, STATUS_DROPPED, claimed_by=claimed_by, reason=reason)


def _finish(record_id: str, status: str, *, claimed_by: str = "", reason: str = "") -> bool:
    conn = get_connection()
    row = conn.execute("SELECT * FROM report_queue WHERE id=?", (record_id,)).fetchone()
    if row is None:
        return False

    payload = dict(_decode(dict(row)).get("payload") or {})
    if reason:
        payload["resolution"] = reason
    now = _now()
    conn.execute(
        "UPDATE report_queue SET status=?, payload=?, acked_at=?, updated_at=?, lease_expires_at=''"
        " WHERE id=?",
        (status, json.dumps(payload, ensure_ascii=False), now, now, record_id),
    )
    conn.commit()
    return True


def reap_expired(now: str = "") -> int:
    """把租约过期的 claimed 记录放回 pending;重试超限的进死信。返回处理条数。

    这是"至少一次"能成立的关键:上游认领后进程崩了,消息不能就此消失。
    """
    conn = get_connection()
    current = now or _now()
    rows = conn.execute(
        "SELECT id, attempts FROM report_queue WHERE status=? AND lease_expires_at != ''"
        " AND lease_expires_at < ?",
        (STATUS_CLAIMED, current),
    ).fetchall()

    for row in rows:
        if int(row["attempts"] or 0) >= MAX_ATTEMPTS:
            conn.execute(
                "UPDATE report_queue SET status=?, updated_at=?, lease_expires_at='' WHERE id=?",
                (STATUS_DEAD, current, row["id"]),
            )
        else:
            conn.execute(
                "UPDATE report_queue SET status=?, claimed_by='', lease_expires_at='', updated_at=?"
                " WHERE id=?",
                (STATUS_PENDING, current, row["id"]),
            )
    if rows:
        conn.commit()
    return len(rows)


def purge_resolved(retention_hours: int = 72, now: str = "") -> int:
    """清理已处理完的记录(保留一段时间便于复盘)。"""
    conn = get_connection()
    cutoff = (
        datetime.now(timezone.utc) - timedelta(hours=max(1, retention_hours))
    ).isoformat()
    cursor = conn.execute(
        "DELETE FROM report_queue WHERE status IN (?, ?) AND updated_at < ?",
        (STATUS_ACKED, STATUS_DROPPED, cutoff),
    )
    conn.commit()
    return cursor.rowcount or 0


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------
def list_reports(*, status: str = "", lane: str = "", conversation_id: str = "", limit: int = 50) -> list[dict[str, Any]]:
    """列出队列记录(调试 / 前端展示用)。"""
    sql = "SELECT * FROM report_queue WHERE 1=1"
    params: list[Any] = []
    if status:
        sql += " AND status=?"
        params.append(status)
    if lane:
        sql += " AND lane=?"
        params.append(lane)
    if conversation_id:
        sql += " AND conversation_id=?"
        params.append(conversation_id)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(max(1, limit))
    rows = get_connection().execute(sql, params).fetchall()
    return [_decode(dict(row)) for row in rows]


def stats() -> dict[str, int]:
    """按状态统计(排障: 队列有没有积压、有没有死信)。"""
    rows = get_connection().execute(
        "SELECT status, COUNT(*) AS n FROM report_queue GROUP BY status"
    ).fetchall()
    return {row["status"]: row["n"] for row in rows}


def _decode(row: dict[str, Any]) -> dict[str, Any]:
    """把库里的 JSON 文本字段解回对象(对外返回可直接用的结构)。"""
    payload = row.get("payload") or ""
    try:
        row["payload"] = json.loads(payload) if payload else {}
    except json.JSONDecodeError:
        row["payload"] = {"raw": payload}
    row["message_ids"] = [m for m in str(row.get("message_ids") or "").split(",") if m]
    return row


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# 观察入口: 一条消息进队列的完整判断(供 main.py 在记录消息后调用)
# ---------------------------------------------------------------------------
# 复核器契约: (候选列表) -> 每条一个判定,形如
#   {"id": "候选ID", "lane": "urgent"|"normal", "summary": "一句话"}
# 由调用方注入(默认用快模型实现),测试注入假实现 —— 本模块不直接依赖 LLM。
Reviewer = Callable[[list[dict[str, Any]]], Awaitable[list[dict[str, Any]]]]


def observe(
    conversation: dict[str, Any],
    *,
    conversation_id: str,
    message_id: str,
    text: str,
    sender_id: str = "",
    sender_name: str = "",
) -> dict[str, Any]:
    """对一条已记录的消息做上报判断(规则初筛,命中则入队为候选)。

    只在会话开着 `alert_enabled` 时调用(成本闸门在调用方)。
    返回 {"matched": bool, "reasons": [...], "status": "..."}。

    规则命中的记录先以 `candidate` 状态入队,等快模型复核决定最终 lane;
    若系统关闭了复核,复核步骤会直接把它提升为 pending(见 flush_candidates)。
    """
    reasons = evaluate_rules(
        conversation,
        text=text,
        sender_id=sender_id,
        sender_name=sender_name,
        chat_type=str(conversation.get("chat_type") or ""),
    )
    if not reasons:
        return {"matched": False, "reasons": [], "status": ""}

    result = enqueue(
        lane=LANE_NORMAL,
        conversation_id=conversation_id,
        message_ids=[message_id],
        payload={
            "summary": (text or "")[:200],
            "reasons": reasons,
            "sender_id": sender_id,
            "sender_name": sender_name,
            "text": (text or "")[:1000],
        },
        dedup_key=message_id,
        status=STATUS_CANDIDATE,
    )
    return {"matched": True, "reasons": reasons, "status": STATUS_CANDIDATE, **result}


# ---------------------------------------------------------------------------
# 快模型复核(批量 + 配额 + 退化)
# ---------------------------------------------------------------------------
def _usage_key() -> str:
    """当日用量计数器的键(按 UTC 日期分桶)。"""
    return f"alert_llm_usage:{datetime.now(timezone.utc).date().isoformat()}"


def review_quota_left() -> int:
    """当日还剩多少次复核调用。未配置预算时返回一个很大的数(等于不限)。"""
    budget = int(get_settings().alert_llm_daily_budget or 0)
    if budget <= 0:
        return 10**9
    used = int(configs_service.get_config(_usage_key(), "0") or 0)
    return max(0, budget - used)


def _record_usage(count: int = 1) -> None:
    key = _usage_key()
    used = int(configs_service.get_config(key, "0") or 0)
    configs_service.set_config(key, str(used + count))


def list_candidates(limit: int = REVIEW_BATCH_SIZE) -> list[dict[str, Any]]:
    """取出待复核的候选(按时间正序)。"""
    rows = get_connection().execute(
        "SELECT * FROM report_queue WHERE status=? ORDER BY created_at ASC LIMIT ?",
        (STATUS_CANDIDATE, max(1, limit)),
    ).fetchall()
    return [_decode(dict(row)) for row in rows]


async def flush_candidates(
    *,
    reviewer: Reviewer | None = None,
    limit: int = REVIEW_BATCH_SIZE,
    max_age_seconds: int = REVIEW_MAX_AGE_SECONDS,
) -> dict[str, Any]:
    """复核一批候选,把它们提升为 pending(或按判定改 lane)。

    什么时候跑: 候选攒够 limit 条,或最老的候选已超过 max_age_seconds
    —— 攒批是为了把多条候选合并成一次模型调用(省成本),
    超时兜底是为了"只有一条候选"时也能及时上报。

    成本控制: 消费前先查当日配额。超额或未注入复核器时**退化为纯规则**:
    直接把候选按 normal 放行,并标注 degraded 原因 —— 宁可多报,不可漏报。
    """
    candidates = list_candidates(limit=limit)
    if not candidates:
        return {"reviewed": 0, "promoted": 0, "degraded": ""}

    # 未攒够且最老的还没超时 → 再等等(省一次调用)
    oldest = min(str(item.get("created_at") or "") for item in candidates)
    young = _seconds_since(oldest) < max_age_seconds
    if len(candidates) < limit and young:
        return {"reviewed": 0, "promoted": 0, "degraded": "", "waiting": True}

    degraded = ""
    verdicts: dict[str, dict[str, Any]] = {}

    if reviewer is None:
        degraded = "no_reviewer"
    elif review_quota_left() <= 0:
        # 配额用尽: 退化,不调用模型,但消息照常上报
        degraded = "quota_exhausted"
    else:
        try:
            results = await reviewer(candidates)
            verdicts = {str(item.get("id")): item for item in results if item.get("id")}
            _record_usage(1)
        except Exception as exc:  # noqa: BLE001 - 复核失败不能丢消息
            degraded = f"review_error:{type(exc).__name__}"
            print(f"[reports] 复核失败,退化为纯规则: {type(exc).__name__}: {exc}")

    promoted = 0
    conn = get_connection()
    now = _now()
    for item in candidates:
        verdict = verdicts.get(str(item["id"])) or {}
        lane = str(verdict.get("lane") or "").strip()
        if lane not in (LANE_URGENT, LANE_NORMAL, LANE_DIGEST, LANE_QUESTION):
            # 退化路径: 规则怎么判就怎么走
            lane = LANE_NORMAL

        payload = dict(item.get("payload") or {})
        if degraded:
            payload["degraded"] = degraded
        if verdict.get("summary"):
            payload["summary"] = str(verdict["summary"])[:500]
        if verdict.get("reason"):
            payload["review_reason"] = str(verdict["reason"])[:200]

        conn.execute(
            "UPDATE report_queue SET lane=?, status=?, payload=?, updated_at=? WHERE id=?",
            (lane, STATUS_PENDING, json.dumps(payload, ensure_ascii=False), now, item["id"]),
        )
        promoted += 1
    conn.commit()
    return {"reviewed": len(candidates), "promoted": promoted, "degraded": degraded}


def _seconds_since(timestamp: str) -> float:
    """距离给定 UTC ISO 时间过了多少秒(解析失败时返回 0 = 视为刚到)。"""
    if not timestamp:
        return 0.0
    try:
        when = datetime.fromisoformat(timestamp)
    except ValueError:
        return 0.0
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - when).total_seconds()
