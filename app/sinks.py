# =============================================================================
# sinks.py - 上报出口(把队列里的上报真正送到人手上)
# -----------------------------------------------------------------------------
# 为什么需要这一层: report_queue 只做到"发现 + 排队",队列本身到不了人。
# 上游 agent 还没接上时,"重要消息自主汇报"端到端是断的 ——
# 消息躺在表里,没有任何东西会把它送出去。
#
# 支持的出口(sink):
#   db  —— 只入队,等上游 claim(默认;也是"什么都不做"的安全选项)
#   qq  —— 转发到指定的 QQ 会话(自己的另一个号 / 专用通知群),本机自闭环
#
# 关键设计: 本地 sink 一旦启用,**它就是这个队列的消费者** ——
#   用 claim 认领(claimed_by="sink:qq"),投递成功后 ack。
#   这样上游不会把同一条再报一次(否则号主会被同一件事打扰两遍)。
#   将来要交给上游,把 alert_sinks 改回 db 即可(改配置,不动代码)。
#
# 可靠性沿用队列语义:
#   · 投递失败不 ack → 租约到期自动回队列重试(不会丢);
#   · 反复失败超过上限 → 进死信(可查,不会无限重试);
#   · 限流暂缓的记录 release 回 pending(下一轮再送,不是丢弃)。
#
# 防刷屏: urgent / question 各发一条(要人立刻看到);
#   normal / digest 合并成一条摘要 —— 话痨群不会把通知渠道刷满。
# =============================================================================

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from . import reports as reports_service
from .config import get_settings
from .services import conversations as conversations_service

# 投递函数契约: (目标会话 ID, 文本) -> 是否送达
Send = Callable[[str, str], Awaitable[bool]]

# 各通道在通知里的说法(给人看的,不是内部术语)
_LANE_LABEL = {
    "urgent": "急",
    "normal": "值得一看",
    "question": "需要你决定",
    "digest": "稍后看",
}

# 需要单独成条的通道: 前者有时间压力,后者等人拍板 —— 混进摘要里容易被忽略
_STANDALONE_LANES = frozenset({"urgent", "question"})


# ---------------------------------------------------------------------------
# 配置解析
# ---------------------------------------------------------------------------
def parse_sinks(raw: str | None = None) -> list[str]:
    """解析启用的出口列表(逗号/分号分隔;空 = 默认 db)。"""
    text = str(raw if raw is not None else get_settings().alert_sinks or "").strip()
    if not text:
        return ["db"]
    return [item.strip().lower() for item in text.replace(";", ",").split(",") if item.strip()]


def parse_forward_target(raw: str | None = None) -> tuple[str, str] | None:
    """解析转发目标,返回 (chat_type, external_id);未配置或格式不对返回 None。

    接受三种写法:
      "123456"              → 私聊
      "private:123456"      → 私聊
      "group:789012"        → 群
      "qq:private:123456"   → 带平台前缀(兼容将来多平台)
    """
    text = str(raw if raw is not None else get_settings().alert_forward_target or "").strip()
    if not text:
        return None
    parts = [part.strip() for part in text.split(":") if part.strip()]
    if len(parts) == 1:
        return ("private", parts[0])
    if len(parts) == 3 and parts[0] == "qq":
        parts = parts[1:]
    if len(parts) == 2 and parts[0] in ("private", "group"):
        return (parts[0], parts[1])
    return None


# ---------------------------------------------------------------------------
# 文案
# ---------------------------------------------------------------------------
def _where(conversation: dict[str, Any]) -> str:
    """会话的可读标识: 优先标题,退回"群/私聊 + 号码"。"""
    title = str(conversation.get("title") or "").strip()
    if title:
        return title
    external = str(conversation.get("external_id") or "").strip()
    if not external:
        return "某个会话"
    return ("群 " if str(conversation.get("chat_type") or "") == "group" else "") + external


def _who(payload: dict[str, Any]) -> str:
    return str(payload.get("sender_name") or payload.get("sender_id") or "").strip()


def _subject(conversation: dict[str, Any], payload: dict[str, Any]) -> str:
    """通知里的"这是谁/哪个会话"。

    私聊里会话标题往往就是对方昵称,再拼一次说话人就成了 "km · km" ——
    重复信息不该占位置,所以相同只显示一次。
    """
    where = _where(conversation)
    who = _who(payload)
    if who and who != where:
        return f"{where} · {who}"
    return where


def format_report(record: dict[str, Any], conversation: dict[str, Any] | None = None) -> str:
    """把一条上报渲染成给人看的通知文本。"""
    payload = dict(record.get("payload") or {})
    label = _LANE_LABEL.get(str(record.get("lane") or ""), "值得一看")
    subject = _subject(conversation or {}, payload)

    lines = [f"【{label}】{subject}"]
    headline = str(payload.get("summary") or "").strip()
    original = str(payload.get("text") or "").strip()
    if headline:
        lines.append(headline)
    if original and original != headline:
        lines.append(f"原话: {original}")
    options = str(payload.get("options") or "").strip()
    if options:
        lines.append(f"选项: {options}")
    reasons = payload.get("reasons") or []
    if reasons:
        lines.append("命中: " + "、".join(str(item) for item in reasons))
    if payload.get("degraded"):
        # 复核没跑成(配额/故障)时会走纯规则上报 —— 让人知道这条没经过模型判断
        lines.append("(复核未执行,按规则上报)")
    return "\n".join(lines)


def format_digest(
    records: list[dict[str, Any]],
    conversations: dict[str, dict[str, Any]] | None = None,
) -> str:
    """把多条普通上报合并成一条摘要(防刷屏)。"""
    conversations = conversations or {}
    lines = [f"【待查看 · {len(records)} 条】"]
    for index, record in enumerate(records, 1):
        payload = dict(record.get("payload") or {})
        conversation = conversations.get(str(record.get("conversation_id") or "")) or {}
        headline = str(payload.get("summary") or payload.get("text") or "").strip()
        lines.append(f"{index}. {_subject(conversation, payload)}")
        if headline:
            lines.append(f"   {headline}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 投递
# ---------------------------------------------------------------------------
async def deliver_pending(
    *,
    send: Send,
    limit: int = 20,
    now: datetime | None = None,
) -> dict[str, Any]:
    """把队列里待投递的上报送给配置的出口(目前只有 qq)。

    参数:
      send: 投递函数 (目标会话 ID, 文本) -> 是否送达。
            由调用方注入(生产: 走 outbox 落库+发送;测试: 假实现)。
      limit: 单轮最多处理多少条(防止一次送太多)。
      now:   当前时间(测试注入用)。

    返回: {"enabled", "delivered", "deferred", "failed", "reason"}
      delivered 按**记录条数**计(不是消息条数)—— 合并发送时一条消息可包含多条记录。
    """
    settings = get_settings()
    result: dict[str, Any] = {
        "enabled": False,
        "delivered": 0,
        "deferred": 0,
        "failed": 0,
        "reason": "",
    }

    if "qq" not in parse_sinks():
        result["reason"] = "qq 出口未启用"
        return result
    target = parse_forward_target()
    if target is None:
        result["reason"] = "alert_forward_target 未配置或格式不对"
        return result

    # 认领即"接管": 本地 sink 一旦启用就是队列的消费者,
    # 认领后上游取不到这些记录 —— 避免同一件事被报两遍。
    #
    # 但**待确认草稿必须排除**: 它是"我们还没发出去的话",
    # 而 sink 的职责是"把通知送到号主手上"。若草稿被当作上报转发出去,
    # 既会污染通知(号主以为收到的是提醒),又会让这条草稿被标记成已处理 ——
    # 真正要发的时候反而没了。草稿只能由人显式确认后发(/api/reports/{id}/send)。
    records = reports_service.claim(
        limit=limit, claimed_by="sink:qq", exclude_lanes=(reports_service.LANE_DRAFT,)
    )
    if not records:
        return result
    result["enabled"] = True

    chat_type, external_id = target
    target_conversation_id = conversations_service.ensure_conversation("qq", chat_type, external_id)
    moment = now or datetime.now(timezone.utc)

    singles = [r for r in records if str(r.get("lane") or "") in _STANDALONE_LANES]
    batch = [r for r in records if str(r.get("lane") or "") not in _STANDALONE_LANES]

    # ---- 限流: 非 urgent 的按会话限每小时条数(urgent 不限 —— 急事不该被压) ----
    cap = int(settings.alert_max_per_hour or 0)
    allowed: list[dict[str, Any]] = []
    deferred: list[dict[str, Any]] = []
    if cap > 0:
        remaining: dict[str, int] = {}
        since = (moment - timedelta(hours=1)).isoformat()
        for record in batch:
            conversation_id = str(record.get("conversation_id") or "")
            if conversation_id not in remaining:
                used = reports_service.count_delivered_since(conversation_id, since)
                remaining[conversation_id] = max(0, cap - used)
            if remaining[conversation_id] > 0:
                remaining[conversation_id] -= 1
                allowed.append(record)
            else:
                deferred.append(record)
    else:
        allowed = batch

    # ---- 单独成条的(急事 / 请示) ----
    for record in singles:
        conversation = conversations_service.get_conversation(str(record.get("conversation_id") or ""))
        text = format_report(record, conversation)
        if await _safe_send(send, target_conversation_id, text):
            reports_service.ack(str(record["id"]), claimed_by="sink:qq")
            result["delivered"] += 1
        else:
            # 不 ack: 租约到期后自动回队列重试(不会丢)
            result["failed"] += 1

    # ---- 合并摘要 ----
    if allowed:
        conversations = {
            str(r.get("conversation_id") or ""): (
                conversations_service.get_conversation(str(r.get("conversation_id") or "")) or {}
            )
            for r in allowed
        }
        text = format_digest(allowed, conversations)
        if await _safe_send(send, target_conversation_id, text):
            for record in allowed:
                reports_service.ack(str(record["id"]), claimed_by="sink:qq")
                result["delivered"] += 1
        else:
            result["failed"] += len(allowed)

    # ---- 限流暂缓的放回队列(有意等待,不是丢弃) ----
    for record in deferred:
        reports_service.release(str(record["id"]), reason="rate-limited")
        result["deferred"] += 1

    return result


async def _safe_send(send: Send, conversation_id: str, text: str) -> bool:
    """投递一次,把异常收敛成 False。

    为什么不让异常冒出去: 一条通知发送失败(如机器人掉线)不该让整轮投递中断 ——
    后面的记录还在队列里等着被处理。失败不 ack,租约到期自然重试。
    """
    try:
        return bool(await send(conversation_id, text))
    except Exception as exc:  # noqa: BLE001 - 投递失败要降级,不能打断本轮
        print(f"[sinks] 投递失败(不丢,等待重试): {type(exc).__name__}: {exc}")
        return False
