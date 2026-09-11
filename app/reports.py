# =============================================================================
# reports.py - 重要消息上报(发现 → 排队;发现由两段流水线完成)
# -----------------------------------------------------------------------------
# 需求来源: "监视还要做到有重要消息自主汇报"; "后台需要上报的消息是要存放在
# 一个类似消息中间件上,也许上游的 agent 看到之后会自动上报"。
#
# 因此本模块的职责边界很明确: **发现 + 排队**,不负责"报给用户"。
# 最终要不要通知人、用什么口吻通知,由消费队列的上游 agent 决定。
#
# 发现流水线(逐级加成本,前三级**零模型成本**):
#   ① 信号提取(app/signals.py): 时间表达解析、动作词表、消息形态 —— 纯字符串;
#   ② 事件判定(app/event_rules.py): 多信号组合成事件类型 + 打分 + 建议通道,
#      组合规则是这一层的关键("改"+"明天四点" = 排期变更,光有"改"不是);
#   ③ 上下文补强(本模块): 连发未回、与进行中目标相关、同类事件聚类合并、
#      高置信直接入队(不等模型)、按到达时间升级紧急度;
#   ④ 快模型复核(可选,见 flush_candidates): 合并成一次调用做最后一道判断,
#      预算耗尽或失败则**沿用 ②③ 的结论**照常上报 —— 宁可多报,不可漏报。
#
# 为什么值得这么长: 监视是异步的,多几段纯代码判断不增加成本、不影响响应,
# 却能把"排期变更"这类真正的事件从闲聊里挑出来。而它们的失效是静默的 ——
# 漏报不会报错,只会让号主永远不知道那条消息。
#
# 配额超限怎么办: 退化为纯规则(候选直接按 ②③ 的结论入队)。
# 宁可多报几条,也不能因为省钱把"急事"漏掉 —— 漏报的代价远大于多打扰一次。
# =============================================================================

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Iterable

from . import event_rules
from . import signals as signals_service
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
LANE_DRAFT = "draft"        # 待确认草稿: agent 拟好但**没发出去**的回复

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
# 信号采集 + 事件判定(前两段流水线)
# ---------------------------------------------------------------------------
# 会话配置里那些"命中即证据"的信号(关键词、白名单、@)。它们不需要组合判断,
# 但需要读配置,所以放在本模块收集,再连同上下文一起交给 event_rules 打分。
def _match_keywords(conversation: dict[str, Any], text: str) -> list[str]:
    """会话配置的上报关键词命中(兼容 JSON 数组与逗号分隔两种写法)。"""
    keywords = conversation.get("alert_keywords") or []
    if isinstance(keywords, str):
        try:
            keywords = (
                json.loads(keywords) if keywords.startswith("[") else [k.strip() for k in keywords.split(",")]
            )
        except json.JSONDecodeError:
            keywords = [k.strip() for k in keywords.split(",")]
    hits: list[str] = []
    for keyword in keywords or []:
        keyword = str(keyword).strip()
        if keyword and keyword in text:
            hits.append(keyword)
    return hits


def _in_watchlist(sender_id: str) -> bool:
    """发送者是否在全局白名单里(有些人说的话天然重要)。"""
    watchlist = _parse_contacts(configs_service.get_config("alert_contacts", ""))
    return bool(watchlist) and str(sender_id) in watchlist


def _unread_streak(conversation_id: str, *, exclude_message_id: str = "", window_hours: int = 6) -> int:
    """对方在本会话连发了几条、期间我们一条都没回。

    "连发未回"是很强的信号: 一条"在吗"不算什么,四条"在吗"就是有人在等回话。
    实现: 从最近的聊天记录往前数,直到遇到我们自己发的那条为止。
    只看最近 window_hours 小时 —— 隔夜的旧消息不该算作"正在催"。
    """
    if not conversation_id:
        return 0
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=window_hours)).isoformat()
    rows = get_connection().execute(
        "SELECT id, role, created_at FROM messages WHERE conversation_id=?"
        " ORDER BY created_at DESC LIMIT 40",
        (conversation_id,),
    ).fetchall()
    count = 0
    for row in rows:
        if str(row["created_at"] or "") < cutoff:
            break
        if str(row["role"] or "") == "assistant":
            break
        if str(row["id"] or "") == exclude_message_id:
            continue
        count += 1
    return count


def analyze(
    conversation: dict[str, Any],
    *,
    text: str,
    sender_id: str = "",
    sender_name: str = "",
    chat_type: str = "",
    unread: int = 0,
    active_goal: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """一条消息 → 信号 + 事件判定(前两段流水线的完整产出)。

    返回 {"signals": ..., "verdict": ..., "reasons": [...], "score": float, "lane": str}
    """
    content = signals_service.normalize(text)
    signals = signals_service.extract_all(content, now=now)
    bot_qq = get_settings().bot_qq
    configured = {
        "at_bot": bool(bot_qq) and f"@{bot_qq}" in content,
        "keywords": _match_keywords(conversation, content),
        "watchlist": _in_watchlist(sender_id),
        "unread": int(unread or 0),
        "active_goal": bool(active_goal),
        "chat_type": str(chat_type or conversation.get("chat_type") or ""),
    }
    verdict = event_rules.classify(signals, configured=configured, now=now)
    return {
        "signals": signals,
        "configured": configured,
        "verdict": verdict,
        "reasons": list(verdict.get("reasons") or []),
        "score": float(verdict.get("score") or 0.0),
        "lane": str(verdict.get("lane") or ""),
        "event": str(verdict.get("event") or ""),
    }


def evaluate_rules(
    conversation: dict[str, Any],
    *,
    text: str,
    sender_id: str = "",
    sender_name: str = "",
    chat_type: str = "",
) -> list[str]:
    """兼容入口: 返回"命中理由"列表(空 = 不候选)。

    保留这个名字是因为它被测试与排障脚本用着;内部已经换成完整的信号 + 判定流水线。
    想知道**为什么**命中、分数多少、建议走哪个通道,请用 analyze()。
    """
    outcome = analyze(
        conversation,
        text=text,
        sender_id=sender_id,
        sender_name=sender_name,
        chat_type=chat_type,
    )
    if not outcome["lane"]:
        return []
    return outcome["reasons"] or ["event:" + outcome["event"]]


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
# 待确认草稿(draft lane)
# ---------------------------------------------------------------------------
def upsert_draft(
    *,
    conversation_id: str,
    text: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """把一条"待确认草稿"放进队列(同一会话只保留最新一条未处理的)。

    为什么是覆盖而不是追加: 草稿是**基于当前上下文**拟出来的,
    对方再发一句,旧草稿就已过时 —— 留着只会让号主在一堆过期版本里挑。
    已处理过的(acked/dropped/dead)不动: 那些是历史,审计要留。

    返回 {"id": str, "updated": bool}。updated=True 表示覆盖了旧草稿。
    """
    conn = get_connection()
    now = _now()
    body = dict(payload or {})
    body["kind"] = "draft"
    body["text"] = text
    body.setdefault("summary", text[:200])

    existing = conn.execute(
        "SELECT id FROM report_queue WHERE conversation_id=? AND lane=? AND status=?",
        (conversation_id, LANE_DRAFT, STATUS_PENDING),
    ).fetchone()
    if existing is not None:
        conn.execute(
            "UPDATE report_queue SET payload=?, attempts=0, claimed_by='',"
            " lease_expires_at='', updated_at=? WHERE id=?",
            (json.dumps(body, ensure_ascii=False), now, existing["id"]),
        )
        conn.commit()
        return {"id": existing["id"], "updated": True}

    created = enqueue(
        lane=LANE_DRAFT,
        conversation_id=conversation_id,
        payload=body,
        status=STATUS_PENDING,
    )
    return {"id": created["id"], "updated": False}


def get_report(record_id: str) -> dict[str, Any] | None:
    """按 ID 取一条记录(不存在返回 None)。"""
    row = get_connection().execute(
        "SELECT * FROM report_queue WHERE id=?", (record_id,)
    ).fetchone()
    return _decode(dict(row)) if row is not None else None


# ---------------------------------------------------------------------------
# 消费(claim / ack / drop)
# ---------------------------------------------------------------------------
def claim(
    *,
    limit: int = 10,
    lane: str = "",
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    claimed_by: str = "upstream",
    exclude_lanes: Iterable[str] = (),
) -> list[dict[str, Any]]:
    """认领待处理记录(至少一次投递语义)。

    流程: 先把过期租约的认领放回 pending(见 reap_expired),
    再原子地取一批 pending → 标记 claimed 并写租约到期时间。

    上游拿到后必须 ack(处理完了)或 drop(决定不报);
    若中途挂了,租约到期后这条会重新变成 pending 供别人取走。

    exclude_lanes: 不认领这些通道。本地 sink 用它挡掉待确认草稿 ——
    草稿是"没发出去的东西",绝不能被当上报自动转发(见 app/sinks.py)。
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
    skipped = [str(item) for item in exclude_lanes if item]
    if skipped:
        sql += f" AND lane NOT IN ({','.join('?' for _ in skipped)})"
        params.extend(skipped)
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


def release(record_id: str, *, reason: str = "") -> bool:
    """把已认领的记录放回 pending(本地 sink 暂缓投递时用)。

    与 drop 的区别: drop 是"决定了不报",release 是"现在不报、等会儿再报"。
    租约到期也会自动回到 pending,但限流是**有意为之的等待** ——
    主动释放能让它下一轮就重新被考虑,而不是干等满租约。
    """
    conn = get_connection()
    row = conn.execute("SELECT * FROM report_queue WHERE id=?", (record_id,)).fetchone()
    if row is None:
        return False
    payload = dict(_decode(dict(row)).get("payload") or {})
    if reason:
        payload["deferred_reason"] = reason
    conn.execute(
        "UPDATE report_queue SET status=?, claimed_by='', lease_expires_at='', payload=?, updated_at=?"
        " WHERE id=?",
        (STATUS_PENDING, json.dumps(payload, ensure_ascii=False), _now(), record_id),
    )
    conn.commit()
    return True


def count_delivered_since(conversation_id: str, since: str) -> int:
    """统计某会话自 since 起已投递(ack)的上报条数 —— 限流判定的依据。

    为什么按 acked_at 而不是 created_at: 要限的是"这段时间打扰了号主几次",
    投递时刻才反映打扰。
    """
    row = get_connection().execute(
        "SELECT COUNT(*) AS n FROM report_queue WHERE conversation_id=? AND status=?"
        " AND acked_at >= ?",
        (conversation_id, STATUS_ACKED, since),
    ).fetchone()
    return int(row["n"] or 0) if row is not None else 0


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
# ---------------------------------------------------------------------------
# 快模型复核(默认实现)
# ---------------------------------------------------------------------------
# 复核提示词单独放在这里而不是 main.py 的组装闭包里,理由有两个:
#   1. 可单测: 组装函数里的闭包没法单独调用,提示词改了也没有测试守着;
#   2. 冒烟脚本(scripts/smoke_llm_paths.py)要验的正是**生产代码路径**——
#      提示词藏在闭包里就只能验一个复制品,等于没验。
REVIEW_SYSTEM_PROMPT = """\
你在帮一个私人助理筛选 QQ 消息,判断哪些值得**立刻打扰号主**。
对每条消息给出判定,只输出 JSON 数组,不要解释:
[{"id":"原样返回","lane":"urgent|normal|digest","summary":"一句话摘要(不超过30字)","reason":"为什么"}]

判定标准:
- urgent: 有时间压力、需要号主尽快回应或决策(约好的事有变、马上要回复的邀请、紧急求助);
- normal: 重要但不紧急,值得知道(有实质内容的信息、需要回但可以晚点);
- digest: 只是被规则误命中,可以攒起来一起看(寒暄、群里的泛泛提问);
拿不准时选 normal(漏报比多报更糟)。"""


def parse_review_verdicts(text: Any) -> list[dict[str, Any]]:
    """解析复核模型的输出(容错: 允许代码块与前后杂讯)。

    解析失败抛 ValueError —— 调用方(flush_candidates)会捕获并**退化为纯规则**,
    消息照常上报。这里刻意不"静默返回空": 空列表在业务上等于"一条都不用报",
    会把重要消息悄悄丢掉。
    """
    raw = str(text or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw[raw.find("[") :] if "[" in raw else raw
    start, end = raw.find("["), raw.rfind("]")
    if start < 0 or end < 0:
        raise ValueError(f"复核结果不是 JSON 数组: {raw[:120]}")
    parsed = json.loads(raw[start : end + 1])
    if not isinstance(parsed, list):
        raise ValueError("复核结果不是数组")
    return [item for item in parsed if isinstance(item, dict)]


def build_default_reviewer(llm_factory: Callable[[bool], Any]) -> Reviewer:
    """用主模型工厂的 fast 通道构造复核器(生产用这个)。"""

    async def reviewer(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        items = [
            {
                "id": str(item.get("id") or ""),
                "text": str((item.get("payload") or {}).get("text") or "")[:500],
                "sender": str((item.get("payload") or {}).get("sender_name") or ""),
                "rules": (item.get("payload") or {}).get("reasons") or [],
            }
            for item in candidates
        ]
        prompt = f"{REVIEW_SYSTEM_PROMPT}\n\n消息列表:\n{json.dumps(items, ensure_ascii=False)}"
        llm = llm_factory(True)
        response = await llm.ainvoke(prompt)
        return parse_review_verdicts(getattr(response, "content", ""))

    return reviewer


Reviewer = Callable[[list[dict[str, Any]]], Awaitable[list[dict[str, Any]]]]


# 同类事件的聚类窗口(秒)。窗口内同一会话的同类事件合并成一条 ——
# 群聊里"改到四点了""那改成四点吧""四点行"往往是一件事,不该报三条。
CLUSTER_WINDOW_SECONDS = 900
# 高置信直通线: 确定性规则已经足够确定时,不必等模型复核 ——
# 既省一次调用,也省掉"攒批等复核"的延迟(排期变更这类事,早一分钟知道有意义)。
AUTO_PROMOTE_SCORE = 0.9


def observe(
    conversation: dict[str, Any],
    *,
    conversation_id: str,
    message_id: str,
    text: str,
    sender_id: str = "",
    sender_name: str = "",
    active_goal: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """对一条已记录的消息做上报判断(信号 → 判定 → 上下文 → 入队)。

    只在会话开着 `alert_enabled` 时调用(成本闸门在调用方)。
    返回 {"matched": bool, "reasons": [...], "status": "...", "event": ..., "score": ...}

    三种去向:
      · 分数不到 digest 线 → 不入队(零动作);
      · 高分且紧急(AUTO_PROMOTE_SCORE 以上) → 直接 pending,不等模型复核;
      · 其余 → candidate,等 flush_candidates 批量复核(或退化为纯规则)。
    """
    chat_type = str(conversation.get("chat_type") or "")
    unread = _unread_streak(conversation_id, exclude_message_id=message_id)
    outcome = analyze(
        conversation,
        text=text,
        sender_id=sender_id,
        sender_name=sender_name,
        chat_type=chat_type,
        unread=unread,
        active_goal=active_goal,
        now=now,
    )
    lane = outcome["lane"]
    if not lane:
        return {"matched": False, "reasons": [], "status": "", "event": "", "score": outcome["score"]}

    verdict = outcome["verdict"]
    nearest = verdict.get("time") or {}
    payload: dict[str, Any] = {
        "summary": (text or "")[:200],
        "reasons": outcome["reasons"],
        "sender_id": sender_id,
        "sender_name": sender_name,
        "text": (text or "")[:1000],
        # 判定结果一并入库: 通知里能解释"为什么报这条",排障时能回溯打分依据
        "event": outcome["event"],
        "score": outcome["score"],
        "suggested_lane": lane,
        "evidence": verdict.get("evidence") or {},
    }
    if nearest.get("at"):
        payload["due_at"] = str(nearest["at"])
        payload["due_text"] = str(nearest.get("raw") or "")

    # ---- 聚类: 同一会话、同类事件、窗口内 → 合并成一条 ----
    merged = _merge_into_cluster(
        conversation_id=conversation_id,
        event=outcome["event"],
        payload=payload,
        now=now,
    )
    if merged is not None:
        return {
            "matched": True,
            "merged": True,
            "reasons": outcome["reasons"],
            "status": merged.get("status") or STATUS_CANDIDATE,
            "event": outcome["event"],
            "score": outcome["score"],
            "id": merged.get("id") or "",
        }

    # ---- 高置信直通: 不等复核 ----
    auto = outcome["score"] >= AUTO_PROMOTE_SCORE and lane == LANE_URGENT
    status = STATUS_PENDING if auto else STATUS_CANDIDATE
    if auto:
        payload["auto_promoted"] = True
    result = enqueue(
        lane=lane,
        conversation_id=conversation_id,
        message_ids=[message_id],
        payload=payload,
        dedup_key=message_id,
        status=status,
    )
    return {
        "matched": True,
        "reasons": outcome["reasons"],
        "status": status,
        "lane": lane,
        "event": outcome["event"],
        "score": outcome["score"],
        "auto_promoted": auto,
        **result,
    }


def _merge_into_cluster(
    *,
    conversation_id: str,
    event: str,
    payload: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """把新事件并进窗口内已有的同类记录(没有就返回 None)。

    合并规则:
      · 只与**同类事件**合并(改期和借钱是两件事,不能揉一起);
      · 只在窗口内合并(半小时前的旧事已经过去了);
      · 保留更高的通道(拆分后有一条是急的,整条就该是急的);
      · 尽力保留最近的时间点(排期变更里最新的时间才是当前结论)。
    """
    if not event or not conversation_id:
        return None
    moment = now or datetime.now(timezone.utc)
    cutoff = (moment - timedelta(seconds=CLUSTER_WINDOW_SECONDS)).isoformat()
    row = get_connection().execute(
        "SELECT * FROM report_queue WHERE conversation_id=? AND status IN (?, ?)"
        " AND created_at >= ? AND lane != ? ORDER BY created_at DESC LIMIT 20",
        (conversation_id, STATUS_CANDIDATE, STATUS_PENDING, cutoff, LANE_DRAFT),
    ).fetchall()

    target = None
    for item in row:
        existing = dict(item)
        try:
            body = json.loads(existing.get("payload") or "{}")
        except json.JSONDecodeError:
            continue
        if str(body.get("event") or "") == event:
            target = (existing, body)
            break
    if target is None:
        return None

    existing, body = target
    body.update(
        {
            "summary": payload["summary"],
            "text": payload["text"],
            "reasons": payload["reasons"],
            "score": max(float(body.get("score") or 0), float(payload.get("score") or 0)),
            "suggested_lane": _stronger_lane(
                str(body.get("suggested_lane") or ""), str(payload.get("suggested_lane") or "")
            ),
            "cluster_count": int(body.get("cluster_count") or 1) + 1,
        }
    )
    if payload.get("due_at"):
        body["due_at"] = payload["due_at"]
        body["due_text"] = payload.get("due_text") or ""
    # 证据合并: 同类事件的证据往往互补("改到" + "取消")
    evidence = dict(body.get("evidence") or {})
    for key, value in (payload.get("evidence") or {}).items():
        if key in evidence and isinstance(evidence[key], list) and isinstance(value, list):
            evidence[key] = list(dict.fromkeys(list(evidence[key]) + list(value)))
        else:
            evidence[key] = value
    body["evidence"] = evidence

    lane = _stronger_lane(str(existing.get("lane") or ""), str(payload.get("suggested_lane") or ""))
    # 合并后重新判定通道: 分数与紧迫度取两者中更强的
    status = existing.get("status") or STATUS_CANDIDATE
    get_connection().execute(
        "UPDATE report_queue SET lane=?, payload=?, updated_at=? WHERE id=?",
        (lane, json.dumps(body, ensure_ascii=False), _now(), existing["id"]),
    )
    get_connection().commit()
    return {"id": existing["id"], "status": status, "lane": lane}


# 通道强弱(用于合并与升级): urgent > normal > digest > 其它
_LANE_RANK = {LANE_URGENT: 3, LANE_NORMAL: 2, LANE_DIGEST: 1}


def _stronger_lane(left: str, right: str) -> str:
    if _LANE_RANK.get(left, 0) >= _LANE_RANK.get(right, 0):
        return left or right
    return right


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
            # 退化为纯规则: **沿用第二段流水线给出的通道**,而不是一律 normal ——
            # 确定性规则已经说了"这条是急的",没道理因为复核没跑成把它降级。
            lane = _stronger_lane(
                str((item.get("payload") or {}).get("suggested_lane") or ""), LANE_NORMAL
            )

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


# ---------------------------------------------------------------------------
# 时间推进(只有异步流水线才做得到的一件事)
# ---------------------------------------------------------------------------
# 事情的紧迫度会**随时间自己变化**: 早上看到的"今晚七点改到八点"是个通知,
# 到傍晚六点还压在队列里就成了"马上就要发生的事"。
# 静态规则只能在消息到达那一刻判一次;而我们是异步的 ——
# 让后台按时间重新评估在队记录: 该升级的升级、该标记过期的标记过期。
ESCALATE_WITHIN_SECONDS = 2 * 3600     # 距离事情发生不到 2 小时 → 升级为紧急


def rescore_pending(
    *,
    within_seconds: int = ESCALATE_WITHIN_SECONDS,
    now: datetime | None = None,
) -> dict[str, Any]:
    """按当前时间重新评估待投递的上报(升级临近的、标记过期的)。

    返回 {"escalated": n, "stale": n}。
    只动 pending 记录: 已被认领/已处理的不再改(消费方可能正在用)。
    """
    moment = now or datetime.now(timezone.utc)
    rows = get_connection().execute(
        "SELECT * FROM report_queue WHERE status=? AND lane IN (?, ?)",
        (STATUS_PENDING, LANE_NORMAL, LANE_DIGEST),
    ).fetchall()

    escalated = 0
    stale = 0
    conn = get_connection()
    for row in rows:
        payload = dict(_decode(dict(row)).get("payload") or {})
        due_at = str(payload.get("due_at") or "")
        if not due_at:
            continue
        try:
            due = datetime.fromisoformat(due_at)
        except ValueError:
            continue
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        delta = (due - moment).total_seconds()

        if 0 <= delta <= within_seconds:
            conn.execute(
                "UPDATE report_queue SET lane=?, payload=?, updated_at=? WHERE id=?",
                (
                    LANE_URGENT,
                    json.dumps({**payload, "escalated": "due_soon"}, ensure_ascii=False),
                    _now(),
                    row["id"],
                ),
            )
            escalated += 1
        elif delta < 0 and not payload.get("stale"):
            # 事情已经过去了: 不改通道(它可能正是"事情已经发生"的重要信息),
            # 只做个标记 —— 让人看到这条时知道时间已过。
            conn.execute(
                "UPDATE report_queue SET payload=?, updated_at=? WHERE id=?",
                (json.dumps({**payload, "stale": True}, ensure_ascii=False), _now(), row["id"]),
            )
            stale += 1
    if escalated or stale:
        conn.commit()
    return {"escalated": escalated, "stale": stale}

