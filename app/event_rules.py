# =============================================================================
# event_rules.py - 上报初筛的"判断层"(第二段,零模型成本)
# -----------------------------------------------------------------------------
# 定位: 拿 app/signals.py 提取的信号,判断"这条消息里有没有值得号主知道的事",
#       给出**事件类型 + 分数 + 建议通道**。不落库、不调模型、不发消息。
#
# 为什么要有分数而不是布尔值:
#   "要不要打扰人"本身是有程度的 —— "@我一下"和"明天组会改到四点、要你确认"
#   不是一回事。分数让下游能按置信度分流(立即报 / 攒着报 / 不报),
#   也让"为什么报了这条"可以回溯(每个加分项都是可展示的证据)。
#
# 打分 = 最强事件的基础分 + 各项加成 - 抑制项(封顶 0~1)。
#   · 基础分来自**组合规则**,不是单个关键词:
#       "改" + 时间 = 排期变更(0.88);只有"改"= 0.5(可能只是"改天再说");
#       "改" + 时间 + 涉及进行中的目标 = 更高(可能推翻已约好的事)。
#   · 加成来自上下文: 时间临近、有紧急词、来自白名单的人、对话已堆积未回…
#   · 抑制项来自"不用管了"这类明确的消解表达 —— 只降分,不直接一票否决
#     (误杀一条真事的代价,远大于多看一条废话)。
# =============================================================================

from __future__ import annotations

from datetime import datetime
from typing import Any

from .signals import has_time_intent, nearest_time

# ---------------------------------------------------------------------------
# 事件类型
# ---------------------------------------------------------------------------
KIND_SCHEDULE_CHANGE = "schedule_change"   # 排期变更 / 取消(最典型的"事件")
KIND_DEADLINE = "deadline"                 # 截止 / 期限
KIND_MONEY = "money"                       # 钱 / 账
KIND_AFFAIR = "affair"                     # 事务性安排(组会、面试、交材料…)
KIND_REQUEST = "request"                   # 请求 / 求助
KIND_INVITATION = "invitation"             # 邀约
KIND_MENTION = "mention"                   # 被 @ / 被点名
KIND_MEDIA = "media"                       # 图片 / 文件 / 语音(得看一眼的东西)
KIND_QUESTION = "question"                 # 疑问(泛泛地问)
KIND_TIME_PLAN = "time_plan"               # 说定了一个具体时间(约见/安排)
KIND_SETTLED = "settled"                   # 明确说"不改了/照旧"(事情定了)
KIND_KEYWORD = "keyword"                   # 会话配置的关键词命中
KIND_WATCHLIST = "watchlist"               # 白名单联系人的消息
KIND_BURST = "burst"                       # 对方连发几条没人理

# 基础分: 命中的事件类型里取最高的一项
BASE_WEIGHTS: dict[str, float] = {
    KIND_SCHEDULE_CHANGE: 0.88,
    KIND_DEADLINE: 0.85,
    KIND_MENTION: 0.75,
    KIND_MONEY: 0.70,
    KIND_KEYWORD: 0.70,
    KIND_WATCHLIST: 0.65,
    KIND_INVITATION: 0.60,
    KIND_AFFAIR: 0.55,
    KIND_BURST: 0.60,
    KIND_MEDIA: 0.50,
    # 说定了一个具体时间,却没动词可说("明天下午三点见""后天上午九点来找你")——
    # 这类消息在监视场景里恰恰是"事件"(时间安排变了/定下来了),必须能报出来。
    # 权重略低: 它只说明"有个时间",不说明这件事跟号主有没有关系。
    KIND_TIME_PLAN: 0.50,
    KIND_REQUEST: 0.50,
    KIND_QUESTION: 0.35,
    KIND_SETTLED: 0.35,
}
# 有些类型光"命中词表"还不够 —— 必须和时间意图组合才算数,
# 否则"改天再说""我们开会吧"这种日常话都会变成事件。
# (下表给的是"组合不成立"时的降级分)
_COMBO_REQUIRED = {
    KIND_SCHEDULE_CHANGE: 0.50,   # 只有"改"没有时间 → 降到 0.5
    KIND_DEADLINE: 0.70,
    KIND_AFFAIR: 0.30,
    KIND_INVITATION: 0.40,
    KIND_REQUEST: 0.45,
}

# 通道阈值(score → lane)。留白区间是"不够格上报",直接不入队(零成本)。
LANE_URGENT_MIN = 0.80
LANE_NORMAL_MIN = 0.55
LANE_DIGEST_MIN = 0.40

# 各加成项的分数
BOOST_URGENCY = 0.15          # "急/尽快/马上"这类词
BOOST_COMMITMENT = 0.08       # "务必/记得/一定要"
BOOST_TIME_IMMINENT = 0.15    # 3 小时内
BOOST_TIME_TODAY = 0.10       # 今天
BOOST_TIME_TOMORROW = 0.05    # 明天
BOOST_DEADLINE_TODAY = 0.10   # 今天到期的截止
BOOST_ACTIVE_GOAL = 0.12      # 与本会话进行中的目标相关
BOOST_PRIVATE = 0.05          # 私聊
PENALTY_GROUP_NOISE = 0.12    # 群里没被点名的泛泛消息
PENALTY_SUPPRESS = 0.30       # "不用管了/已解决"

# 群聊里这些类型的"噪音"最明显: 一群人聊得热闹,但对号主未必是事
_NOISY_GROUP_KINDS = (KIND_QUESTION, KIND_REQUEST, KIND_INVITATION, KIND_MEDIA)


def _time_pressure(times: list[dict[str, Any]], now: datetime) -> tuple[float, str]:
    """按"离现在还有多久"给加成,并给一句可展示的理由。

    只用**最近的**那次时间判断(一条消息里最要紧的就是最近的那个时间点)。
    已经过去的时间不加成也不惩罚 —— 它可能正是"事情已经发生"的信号,
    由事件类型本身去表达。
    """
    if not times:
        return 0.0, ""
    today_str = now.strftime("%Y-%m-%d")
    nearest = min(
        (item for item in times if item.get("hours") is not None),
        key=lambda item: abs(float(item["hours"])),
        default=None,
    )
    if nearest is None:
        return 0.0, ""
    hours = float(nearest["hours"])
    when = str(nearest.get("raw") or "")
    if 0 <= hours <= 3:
        return BOOST_TIME_IMMINENT, f"time_soon:{when}"
    if str(nearest.get("date") or "") == today_str:
        # 今天的(含已经过去的今天)—— "今天的事"本身就值得优先知道
        return BOOST_TIME_TODAY, f"time_today:{when}"
    if 0 < hours <= 48:
        return BOOST_TIME_TOMORROW, f"time_soon_day:{when}"
    return 0.0, ""


def classify(
    signals: dict[str, Any],
    *,
    configured: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """信号 → 事件判定(类型、分数、建议通道、可展示的证据)。

    configured 是"从配置/上下文来"的信号,由调用方(reports.observe)收集:
      at_bot      bool   是否 @ 了机器人/号主
      keywords    list   会话配置的 alert_keywords 命中
      watchlist   bool   发送者在 alert_contacts 白名单里
      unread      int    对方在本会话连续发了多少条、期间没有我们的回复
      active_goal bool   本会话有进行中的目标
      chat_type   str    private / group
    """
    configured = configured or {}
    now = now or datetime.now()
    actions = (signals.get("actions") or {})
    times = list(signals.get("times") or [])
    shape = signals.get("shape") or {}
    amounts = list(signals.get("amounts") or [])
    chat_type = str(configured.get("chat_type") or "")

    def words(key: str) -> list[str]:
        return [str(item.get("word") or "") for item in (actions.get(key) or [])]

    reasons: list[str] = []
    candidates: dict[str, float] = {}
    evidence: dict[str, Any] = {}
    has_time = has_time_intent(signals)

    # ---- 配置/上下文类信号(它们本身就是结论,不需要组合) ----
    if configured.get("at_bot"):
        candidates[KIND_MENTION] = BASE_WEIGHTS[KIND_MENTION]
        reasons.append("at_bot")
    keywords = [str(item) for item in (configured.get("keywords") or []) if str(item)]
    if keywords:
        score = min(0.85, BASE_WEIGHTS[KIND_KEYWORD] + 0.05 * (len(keywords) - 1))
        candidates[KIND_KEYWORD] = score
        reasons.extend(f"keyword:{item}" for item in keywords)
    if configured.get("watchlist"):
        candidates[KIND_WATCHLIST] = BASE_WEIGHTS[KIND_WATCHLIST]
        reasons.append("watchlist")

    # ---- 排期变更: 变更词 + 时间意图 ----
    change_words = words("change")
    if change_words:
        weight = BASE_WEIGHTS[KIND_SCHEDULE_CHANGE] if has_time else _COMBO_REQUIRED[KIND_SCHEDULE_CHANGE]
        candidates[KIND_SCHEDULE_CHANGE] = weight
        evidence["change"] = change_words
        reasons.append("event:schedule_change")
        reasons.extend(f"change:{item}" for item in change_words[:2])
    settled_words = words("settled")
    if settled_words:
        candidates[KIND_SETTLED] = BASE_WEIGHTS[KIND_SETTLED]
        evidence["settled"] = settled_words
        reasons.append("event:settled")

    # ---- 截止 / 期限 ----
    deadlines = [item for item in times if item.get("deadline")]
    if deadlines:
        # 有"具体时间"的截止(周五/9月12号)比只有模糊期限("月底""尽快")更值得立刻看
        if any(item.get("resolved") for item in deadlines):
            candidates[KIND_DEADLINE] = BASE_WEIGHTS[KIND_DEADLINE]
        else:
            candidates[KIND_DEADLINE] = _COMBO_REQUIRED[KIND_DEADLINE]
        evidence["deadline"] = [str(item.get("raw") or "") for item in deadlines[:2]]
        reasons.append("event:deadline")

    # ---- 钱 / 账 ----
    money_words = words("money")
    if money_words or amounts:
        candidates[KIND_MONEY] = BASE_WEIGHTS[KIND_MONEY]
        evidence["money"] = money_words or [str(item["raw"]) for item in amounts]
        reasons.append("event:money")

    # ---- 事务性安排(组会/面试/交材料…) ----
    affair_words = words("affair")
    if affair_words:
        weight = BASE_WEIGHTS[KIND_AFFAIR] if has_time else _COMBO_REQUIRED[KIND_AFFAIR]
        candidates[KIND_AFFAIR] = weight
        evidence["affair"] = affair_words
        reasons.append("event:affair")

    # ---- 邀约 ----
    invite_words = words("invite")
    if invite_words:
        weight = (
            BASE_WEIGHTS[KIND_INVITATION]
            if (has_time or words("question"))
            else _COMBO_REQUIRED[KIND_INVITATION]
        )
        candidates[KIND_INVITATION] = weight
        evidence["invite"] = invite_words

    # ---- 请求 / 求助 ----
    request_words = words("request")
    if request_words:
        weight = (
            BASE_WEIGHTS[KIND_REQUEST] if has_time else _COMBO_REQUIRED[KIND_REQUEST]
        )
        candidates[KIND_REQUEST] = weight
        evidence["request"] = request_words

    # ---- 媒体(图/文件/语音) ----
    if shape.get("media_only"):
        candidates[KIND_MEDIA] = BASE_WEIGHTS[KIND_MEDIA]
        reasons.append("media")
    elif shape.get("has_media"):
        candidates[KIND_MEDIA] = 0.35

    # ---- 疑问 ----
    question_words = words("question")
    if question_words:
        candidates[KIND_QUESTION] = BASE_WEIGHTS[KIND_QUESTION]
        reasons.append(f"pattern:{question_words[0]}")

    # ---- 说定了一个具体时间(有时间、没动词) ----
    # 只认**已经解析出时刻/日期**且在未来(或刚刚过去)的时间表达 ——
    # "改天""回头""月底"这类模糊说法不算,它们没有安排的含义。
    # 这条规则是"排期类事件"的兜底: 缺了它,"明天下午三点见"会完全消失。
    resolved_future = [
        item
        for item in times
        if item.get("resolved")
        and item.get("hours") is not None
        and -6 <= float(item["hours"]) <= 24 * 21
    ]
    if resolved_future:
        candidates[KIND_TIME_PLAN] = BASE_WEIGHTS[KIND_TIME_PLAN]
        evidence["time_plan"] = [str(item.get("raw") or "") for item in resolved_future[:2]]

    # ---- 连发未回(上下文) ----
    unread = int(configured.get("unread") or 0)
    if unread >= 3:
        candidates[KIND_BURST] = min(0.70, BASE_WEIGHTS[KIND_BURST] + 0.05 * (unread - 3))
        evidence["unread"] = unread
        reasons.append(f"burst:{unread}")

    if not candidates:
        return {
            "event": "",
            "score": 0.0,
            "lane": "",
            "reasons": [],
            "evidence": {},
            "time": None,
            "boost": [],
        }

    event = max(candidates, key=lambda key: candidates[key])
    score = candidates[event]
    boost: list[str] = []

    # ---- 加成 ----
    urgency = words("urgency")
    if urgency:
        score += BOOST_URGENCY
        boost.append(f"urgent_word:{urgency[0]}")
    commitment = words("commitment")
    if commitment:
        score += BOOST_COMMITMENT
        boost.append(f"commitment:{commitment[0]}")

    pressure, pressure_reason = _time_pressure(times, now)
    if pressure:
        score += pressure
        boost.append(pressure_reason)
    # 截止 + 就在今天 → 额外的紧迫(过期就来不及了)
    if deadlines and any(
        str(item.get("date") or "") == now.strftime("%Y-%m-%d") for item in deadlines
    ):
        score += BOOST_DEADLINE_TODAY
        boost.append("deadline_today")

    if configured.get("active_goal") and event in (
        KIND_SCHEDULE_CHANGE, KIND_DEADLINE, KIND_AFFAIR, KIND_INVITATION, KIND_MONEY,
        KIND_TIME_PLAN,
    ):
        score += BOOST_ACTIVE_GOAL
        boost.append("active_goal")

    if chat_type == "private":
        score += BOOST_PRIVATE
    elif chat_type == "group":
        penalized = bool(configured.get("at_bot")) or bool(keywords) or bool(configured.get("watchlist"))
        if not penalized and event in _NOISY_GROUP_KINDS:
            score -= PENALTY_GROUP_NOISE
            boost.append("group_noise")

    suppress = words("suppress")
    if suppress:
        score -= PENALTY_SUPPRESS
        boost.append(f"suppress:{suppress[0]}")

    score = max(0.0, min(1.0, round(score, 3)))
    lane = lane_for_score(score)

    # ---- 紧急闸门: 分数高不等于"现在就得打扰人" ----
    # urgent 的语义是"立刻告诉号主"。要走到这里,必须有**为什么是现在**的理由:
    # 时间临近、明确说了急、或者截止就在今天 —— 否则再高的分也只进 normal。
    # (否则"下周三有个截止"这种几周后的事也会变成催命符。)
    deadline_today = bool(deadlines) and any(
        str(item.get("date") or "") == now.strftime("%Y-%m-%d") for item in deadlines
    )
    urgent_ok = bool(pressure_reason or urgency or deadline_today)
    if lane == "urgent" and not urgent_ok:
        lane = "normal"
        boost.append("not_urgent:no_time_pressure")

    reasons.extend(boost[index] for index in range(min(2, len(boost))))
    # 纯标点结巴出来的"命中"没有信息量,有别的理由时就别占位置
    if len(reasons) > 1:
        reasons = [item for item in reasons if item not in ("pattern:?", "pattern:？")]
    nearest = nearest_time(signals)
    return {
        "event": event,
        "score": score,
        "lane": lane,
        "reasons": reasons,
        "evidence": evidence,
        "time": nearest,
        "boost": boost,
    }


def lane_for_score(score: float) -> str:
    """分数 → 通道(空串 = 不够格,不入队)。

    与 reports.LANE_* 是同一套取值,但这里不 import reports ——
    判断层不该依赖存储层(将来换存储、换 MQ 都不用动这一层)。
    """
    if score >= LANE_URGENT_MIN:
        return "urgent"
    if score >= LANE_NORMAL_MIN:
        return "normal"
    if score >= LANE_DIGEST_MIN:
        return "digest"
    return ""


__all__ = [
    "KIND_SCHEDULE_CHANGE",
    "KIND_DEADLINE",
    "KIND_MONEY",
    "KIND_AFFAIR",
    "KIND_REQUEST",
    "KIND_INVITATION",
    "KIND_MENTION",
    "KIND_MEDIA",
    "KIND_QUESTION",
    "KIND_TIME_PLAN",
    "KIND_SETTLED",
    "KIND_KEYWORD",
    "KIND_WATCHLIST",
    "KIND_BURST",
    "BASE_WEIGHTS",
    "classify",
    "lane_for_score",
]
