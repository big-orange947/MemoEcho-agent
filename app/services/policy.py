# =============================================================================
# services/policy.py - 会话策略(值守 / 监视 / 上报的开关中心)
# -----------------------------------------------------------------------------
# 为什么需要这一层:
#   上线初期,"要不要回复"只由**平台规则**决定(私聊必回、群聊被 @ 才回),
#   于是所有私聊都在替号主说话 —— 既不能按会话开关,也没法"只看不回"。
#   本模块把这件事收口成**会话级策略**,并给出统一的判定函数。
#
# 三个开关(默认全关,由用户显式开启):
#   monitor     总开关: 关 ⇒ 不落库、不记忆、不上报、不回复(仅审计留痕)
#   reply_mode  off 不回复 / draft 草稿待确认 / auto 自动回复
#   alert_enabled  是否把重要消息投进上报队列
#
# 权威规则: **显式指令 > 会话策略**
#   主 agent 派发、桌面端命令是"人明确要求做的事",不受开关约束 ——
#   否则"帮我约 km 打游戏"在未开启自动回复的会话里根本推进不下去。
#
# 字段命名刻意对齐旧仓库(AUTO_REPLY/DRAFT_ONLY/SILENT、notificationKeywords、
# digestWindowSeconds…),将来接前端配置面板不需要改协议。
# =============================================================================

from __future__ import annotations

import json
import uuid
from typing import Any, Mapping

from ..db import get_connection
from ..events import Event, EventKind, EventSource
from . import eventlog

# 回复模式取值(与旧仓库 AUTO_REPLY / DRAFT_ONLY / SILENT 对应)
REPLY_MODES = ("off", "draft", "auto")

# 策略字段清单(API 层与序列化共用一份,避免各处漏字段)
POLICY_FIELDS = (
    "monitor",
    "reply_mode",
    "alert_enabled",
    "alert_keywords",
    "require_human_confirmation",
    "digest_window_seconds",
    "digest_max_messages",
    "allowed_tools",
)

# 新建会话的默认策略: 全关(宁可不做,也不擅自替号主说话)
DEFAULTS: dict[str, Any] = {
    "monitor": 0,
    "reply_mode": "off",
    "alert_enabled": 0,
    "alert_keywords": [],
    "require_human_confirmation": 1,
    "digest_window_seconds": 1800,
    "digest_max_messages": 20,
    "allowed_tools": [],   # 空 = 按会话类型取默认集(见 resolve_allowed_tools)
}

# 高危工具: 会对系统外产生不可逆影响(尤其是"以号主身份对外发消息")。
# 判定优先看工具自带的 high_risk 标签(新工具只需打标签),
# 这里再保留一份名字兜底 —— 防止某个工具忘打标签就被群里放行。
HIGH_RISK_TAG = "high_risk"
HIGH_RISK_TOOLS = frozenset({"send_qq_message"})

# 群聊默认不给的高危工具。
# 理由: 群聊人数多、不可控,被诱导"替号主发消息"的风险最高;
# 私聊/桌面线程默认不限制(现有"帮问小号""转告 km"等流程不受影响)。
GROUP_DENIED_BY_DEFAULT = HIGH_RISK_TOOLS

# 判定结果(decide 的返回值)
DECISION_REPLY = "reply"    # 跑图(记录 + 推理 + 回复)
DECISION_RECORD = "record"  # 只落库(供攒批总结与上报,不跑图)
DECISION_IGNORE = "ignore"  # 什么都不做(仅审计留痕)

# 显式指令来源: 主 agent 调度(agent)与桌面端/前端命令(desktop)。
# 这两类事件是"人明确要求的动作",不受会话开关约束。
_EXPLICIT_SOURCES = frozenset({EventSource.AGENT, EventSource.DESKTOP})


# ---------------------------------------------------------------------------
# 归一化
# ---------------------------------------------------------------------------
def _as_bool_int(value: Any) -> int:
    """把各种写法统一成 0/1(兼容 true/false/"1"/1)。"""
    if isinstance(value, str):
        return 1 if value.strip().lower() in ("1", "true", "yes", "on") else 0
    return 1 if value else 0


def _parse_list(raw: Any) -> list[str]:
    """把 JSON 数组 / 逗号或换行分隔的字符串统一成字符串列表。"""
    if isinstance(raw, (list, tuple)):
        return [str(item).strip() for item in raw if str(item).strip()]
    text = str(raw or "").strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [str(item).strip() for item in parsed if str(item).strip()]
        except json.JSONDecodeError:
            pass
    # 兼容用户手写 "急事,改时间" 或按行分隔的写法
    return [item.strip() for item in text.replace("\n", ",").split(",") if item.strip()]


def _row_to_policy(row: dict[str, Any]) -> dict[str, Any]:
    """把数据库行归一化成策略字典(类型稳定,供直接序列化给前端)。

    布尔字段统一成 Python 真值(bool),而不是库里的 0/1 ——
    API 返回 JSON 时是 true/false,前端与上游不用再猜语义。
    """
    return {
        "monitor": bool(_as_bool_int(row.get("monitor"))),
        "reply_mode": str(row.get("reply_mode") or "off") if row.get("reply_mode") in REPLY_MODES else "off",
        "alert_enabled": bool(_as_bool_int(row.get("alert_enabled"))),
        "alert_keywords": _parse_list(row.get("alert_keywords")),
        "require_human_confirmation": bool(_as_bool_int(row.get("require_human_confirmation"))),
        "digest_window_seconds": int(row.get("digest_window_seconds") or DEFAULTS["digest_window_seconds"]),
        "digest_max_messages": int(row.get("digest_max_messages") or DEFAULTS["digest_max_messages"]),
        "allowed_tools": _parse_list(row.get("allowed_tools")),
    }


# ---------------------------------------------------------------------------
# 读取
# ---------------------------------------------------------------------------
def normalize_policy(row: dict[str, Any]) -> dict[str, Any]:
    """把数据库行归一化成策略字典(公开入口,API/前端直接序列化)。"""
    return _row_to_policy(row)


def get_policy(conversation_id: str) -> dict[str, Any]:
    """读取会话策略(会话不存在时返回默认值,不抛异常)。"""
    row = get_connection().execute(
        "SELECT * FROM conversations WHERE id=?", (conversation_id,)
    ).fetchone()
    return _row_to_policy(dict(row) if row is not None else {})


def is_monitored(conversation: dict[str, Any]) -> bool:
    """该会话是否处于"监视"状态(显式开启,或被回复/上报隐含开启)。"""
    return bool(conversation.get("monitor"))


# ---------------------------------------------------------------------------
# 写入
# ---------------------------------------------------------------------------
def update_policy(conversation_id: str, **changes: Any) -> dict[str, Any]:
    """更新会话策略并写审计。

    参数(只传要改的字段,其余保持不变):
      monitor / reply_mode / alert_enabled / alert_keywords /
      require_human_confirmation / digest_window_seconds / digest_max_messages /
      allowed_tools

    返回: {"policy": 新策略, "changed": {字段: [旧值, 新值]}, "implied": [被自动打开的字段]}

    蕴含关系: reply_mode≠off 或 alert_enabled=1 ⇒ 自动打开 monitor
    (不监视就没数据,谈不上回复或上报)。这里**显式**把它写进库并回报,
    而不是静默改语义 —— 调用方要能看见"多开了一个开关"。

    值校验失败抛 ValueError(API 层转 400)。
    """
    conn = get_connection()
    row = conn.execute("SELECT * FROM conversations WHERE id=?", (conversation_id,)).fetchone()
    if row is None:
        raise ValueError(f"会话不存在: {conversation_id}")

    current = _row_to_policy(dict(row))
    updated = dict(current)

    # ---- 逐字段校验与赋值 ----
    for field, value in changes.items():
        if field not in POLICY_FIELDS:
            raise ValueError(f"不支持的策略字段: {field}")
        if value is None:
            continue

        if field == "reply_mode":
            mode = str(value).strip().lower()
            if mode not in REPLY_MODES:
                raise ValueError(f"reply_mode 必须是 {list(REPLY_MODES)} 之一")
            updated[field] = mode
        elif field in ("monitor", "alert_enabled", "require_human_confirmation"):
            # 统一成 bool(对外/审计里显示一致;落库时再转 0/1)
            updated[field] = bool(_as_bool_int(value))
        elif field == "alert_keywords" or field == "allowed_tools":
            updated[field] = _parse_list(value)
        elif field in ("digest_window_seconds", "digest_max_messages"):
            try:
                number = int(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{field} 必须是整数") from exc
            if number <= 0:
                raise ValueError(f"{field} 必须为正整数")
            updated[field] = number

    # ---- 蕴含关系: 要回复/要上报 ⇒ 必须先监视 ----
    implied: list[str] = []
    if updated["reply_mode"] != "off" or updated["alert_enabled"]:
        if not updated["monitor"]:
            updated["monitor"] = True
            implied.append("monitor")

    # ---- 落库 ----
    changed = {
        field: [current[field], updated[field]]
        for field in POLICY_FIELDS
        if current[field] != updated[field]
    }
    if changed:
        conn.execute(
            "UPDATE conversations SET monitor=?, reply_mode=?, alert_enabled=?, alert_keywords=?,"
            " require_human_confirmation=?, digest_window_seconds=?, digest_max_messages=?,"
            " allowed_tools=?, updated_at=? WHERE id=?",
            (
                int(updated["monitor"]),
                updated["reply_mode"],
                int(updated["alert_enabled"]),
                json.dumps(updated["alert_keywords"], ensure_ascii=False),
                int(updated["require_human_confirmation"]),
                updated["digest_window_seconds"],
                updated["digest_max_messages"],
                json.dumps(updated["allowed_tools"], ensure_ascii=False) if updated["allowed_tools"] else "",
                _now(),
                conversation_id,
            ),
        )
        conn.commit()
        _audit(conversation_id, changed, implied)

    return {"policy": updated, "changed": changed, "implied": implied}


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _audit(conversation_id: str, changed: dict[str, list[Any]], implied: list[str]) -> None:
    """把策略变更写进审计表(谁在什么时候把哪个开关从什么改成了什么)。"""
    parts = [f"{field}: {old} → {new}" for field, (old, new) in changed.items()]
    summary = "策略变更 " + "; ".join(parts)
    if implied:
        summary += f" (自动打开: {', '.join(implied)})"
    eventlog.log_event(
        Event(
            event_id=f"policy-{uuid.uuid4().hex}",
            source=EventSource.SYSTEM,
            kind=EventKind.SYSTEM,
            should_respond=False,
            conversation_id=conversation_id,
            text=summary,
        ),
        conversation_id=conversation_id,
    )


# ---------------------------------------------------------------------------
# 工具权限
# ---------------------------------------------------------------------------
def is_high_risk(tool_name: str, tags: Any = ()) -> bool:
    """该工具是否属于高危(会被群聊默认拒绝)。"""
    return HIGH_RISK_TAG in {str(tag) for tag in (tags or ())} or tool_name in HIGH_RISK_TOOLS


def resolve_allowed_tools(
    conversation: dict[str, Any],
    registry: Mapping[str, Any],
) -> set[str]:
    """算出该会话最终可用的工具集合。

    registry = {工具名: 工具标签} —— 由调用方从工具层收集后传入,
    避免本模块依赖具体的工具实现。

    规则:
      · 会话显式配了 allowed_tools → 取交集(配了名字但没注册的忽略);
      · 没配 → 按会话类型取默认集: 群聊默认剔除高危工具(如 send_qq_message)。
    """
    explicit = _parse_list(conversation.get("allowed_tools"))
    if explicit:
        wanted = set(explicit)
        return {name for name in registry if name in wanted}

    if str(conversation.get("chat_type") or "") == "group":
        return {name for name, tags in registry.items() if not is_high_risk(name, tags)}
    return set(registry)


def tool_allowed(conversation: dict[str, Any], tool_name: str, tags: Any = ()) -> bool:
    """单个工具的授权判断(供高危工具**内部**兜底校验,与 act 节点形成双保险)。"""
    explicit = _parse_list(conversation.get("allowed_tools"))
    if explicit:
        return tool_name in set(explicit)
    if str(conversation.get("chat_type") or "") == "group":
        return not is_high_risk(tool_name, tags)
    return True


# ---------------------------------------------------------------------------
# 分流判定(agent_handler 的唯一决策入口)
# ---------------------------------------------------------------------------
def decide(
    event: Event,
    conversation: dict[str, Any],
    *,
    has_active_goal: bool = False,
) -> tuple[str, str]:
    """判定一个事件该怎么处理,返回 (判定结果, 原因)。

    判定结果见 DECISION_* 常量。原因字符串会进日志/审计,便于回答
    "这条消息为什么没回/为什么被记录了"。

    判定顺序(自上而下,先命中先返回):
      1. 定时唤醒: 只在有进行中的目标、或会话开着自动回复时继续(否则是残留计划);
      2. 平台要求回应 + (显式指令 / 任务授权 / auto) → 跑图;
      3. 监视中的会话 → 只落库(攒批总结与上报的数据来源);
      4. 其余 → 什么都不做。
    """
    explicit = event.source in _EXPLICIT_SOURCES
    # 任务授权态: 显式指令建了目标之后,该会话在任务结束前可以自由交流
    monitored = bool(conversation.get("monitor")) or explicit or has_active_goal

    # ---- 1. 定时唤醒 ----
    if event.kind == EventKind.TIMER:
        if has_active_goal:
            return DECISION_REPLY, "timer-active-goal"
        if conversation.get("reply_mode") == "auto":
            return DECISION_REPLY, "timer-reply-mode-auto"
        return DECISION_IGNORE, "timer-no-context"

    # ---- 2. 跑图 ----
    if event.should_respond and (
        explicit or has_active_goal or conversation.get("reply_mode") == "auto"
    ):
        if explicit:
            return DECISION_REPLY, "explicit-instruction"
        if has_active_goal:
            return DECISION_REPLY, "task-authorization"
        return DECISION_REPLY, "reply-mode-auto"

    # ---- 3. 只记录 ----
    if monitored:
        return DECISION_RECORD, "monitor-on"

    # ---- 4. 忽略 ----
    return DECISION_IGNORE, "monitor-off"
