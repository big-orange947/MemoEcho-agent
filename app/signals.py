# =============================================================================
# signals.py - 上报初筛的"信号提取层"(第一段,零模型成本)
# -----------------------------------------------------------------------------
# 定位: 把一条消息拆成**结构化的信号**,不做"要不要上报"的判断 ——
#       那是第二段(app/event_rules.py)的事。
#
# 为什么单独一层:
#   判断"这条消息里有没有事"靠的不是单个关键词,而是信号的**组合**——
#   "改"字本身很常见("改天再说"),但"改"+"明天下午四点"就是一次排期变更。
#   把提取和判断分开,判断规则就能写得又长又清楚,而且每条规则都能单独测。
#
# 五个信号族:
#   ① 时间(time)   —— 中文时间表达解析成日期/时刻,并算出"离现在还有多久"
#   ② 动作(action) —— 变更/事务/金钱/请求/邀约/紧急/承诺/否定 等词表命中
#   ③ 形态(shape)  —— 有没有图/文件/语音/@/引用;是不是只有个表情包
#   ④ 位置(pos)    —— 命中词在原文中的位置(供"组合规则"判断是否相邻)
#   ⑤ 量(amount)   —— 金额、次数等数字线索
#
# 设计取舍:
#   · 宁可多给信号,别在提取阶段就下结论 —— 错误的判断后面还能被修正,
#     没提取到的信号则永远丢了;
#   · 全部纯字符串运算,无网络无模型 —— 可以对监视中的每条消息全量跑。
# =============================================================================

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

# ---------------------------------------------------------------------------
# 归一化
# ---------------------------------------------------------------------------
_ZERO_WIDTH = re.compile(r"[\u200b-\u200f\u202a-\u202e\ufeff]")
_WS = re.compile(r"\s+")
# 全角 ASCII(！？～,,等)统一成半角,便于词表与正则只写一套。
# 注意:CJK 标点里的 、。"「」 不在范围内(它们不是 ASCII 的全角形态)。
_FULLWIDTH_MAP = {chr(0xFF01 + i): chr(0x21 + i) for i in range(94)}
_FULLWIDTH_MAP["　"] = " "


def normalize(text: str) -> str:
    """归一化: 去零宽字符、全角转半角、折叠空白。

    为什么必须做: 手机上打出的 "７点" 与 "7点"、"！！" 与 "!!" 是同一件事,
    但字符串比较完全不同 —— 不归一会让词表和正则漏掉真实命中。
    """
    if not text:
        return ""
    out = _ZERO_WIDTH.sub("", str(text))
    out = "".join(_FULLWIDTH_MAP.get(ch, ch) for ch in out)
    return _WS.sub(" ", out).strip()


# ---------------------------------------------------------------------------
# 形态: 非文本内容与消息形态
# ---------------------------------------------------------------------------
# 与 content.py 的渲染占位符对齐(见 _PLACEHOLDERS)。
_MEDIA_TOKENS = ("[图片]", "[视频]", "[语音]", "[文件]", "[合并转发]", "[卡片]")
_EMOJI_ONLY = re.compile(r"^\[(表情|骰子|猜拳|戳一戳|未知消息)\]$")


def shape(text: str) -> dict[str, Any]:
    """消息形态: 有没有媒体、是不是引用回复、文本长度等。

    只看渲染后的文本(占位符即内容类型),不依赖平台原始段 ——
    这样任何平台的消息进来都能用同一套判断。
    """
    content = text or ""
    media = [token for token in _MEDIA_TOKENS if token in content]
    stripped = content
    for token in _MEDIA_TOKENS + ("[表情]", "[回复]", "[骰子]", "[猜拳]", "[戳一戳]"):
        stripped = stripped.replace(token, "")
    stripped = stripped.strip()
    return {
        "has_media": bool(media),
        "media": media,
        # 只有媒体、几乎没文字 → "发个图不说话",值得看一眼但不紧急
        "media_only": bool(media) and len(stripped) <= 2,
        "is_reply": "[回复]" in content,
        "is_emoji_only": bool(_EMOJI_ONLY.match(content.strip())),
        "text_len": len(content),
        "stripped_len": len(stripped),
    }


# ---------------------------------------------------------------------------
# 时间表达解析
# ---------------------------------------------------------------------------
# 为什么值得认真做: "什么时候"才是消息"是不是事件"的关键维度。
# "改到四点" 和 "改天再说" 里都有关键词,只有前者是真的排期变更。
_PERIODS: dict[str, str] = {
    "凌晨": "dawn", "半夜": "dawn", "清晨": "morning", "早上": "morning",
    "上午": "morning", "中午": "noon", "下午": "afternoon", "傍晚": "evening",
    "晚上": "evening", "夜里": "evening",
}
_DAY_WORDS: dict[str, int] = {
    "大后天": 3, "后天": 2, "明天": 1, "明日": 1, "今天": 0, "今日": 0,
    "昨天": -1, "昨日": -1, "前天": -2,
}
# "哪天 + 哪个时段"连写的词(今晚、明早): 拆成一件事,别漏了"哪一天"
_DAY_PERIOD_WORDS: dict[str, tuple[int, str]] = {
    "今晚": (0, "evening"), "今夜": (0, "evening"), "明晚": (1, "evening"),
    "今早": (0, "morning"), "明早": (1, "morning"), "每天": (0, ""),
}
_WEEKDAY_CHARS = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6, "末": 5}
_DEADLINE_WORDS = ("截止", "ddl", "deadline", "最晚", "不晚于", "之前", "以前", "前交", "期限内", "限时")

_RE_CLOCK = re.compile(r"(\d{1,2})\s*[:：]\s*(\d{2})")
# 中文数字与阿拉伯数字都要认: 群里更常见的写法是"七点半""三点见"。
_CN_NUM = "零一二三四五六七八九十两"
_RE_CLOCK_CN = re.compile(rf"([{_CN_NUM}\d]{{1,3}})\s*[点時时]\s*(半|一刻|三刻|[零一二三四五六七八九十两\d]{{1,3}}\s*分?)?")
_RE_WEEKDAY = re.compile(r"(这|本|下|下个|上)?\s*(?:周|星期|礼拜)\s*([一二三四五六日天末])")
_RE_DATE_ABS = re.compile(r"(?:(\d{4})\s*年)?\s*(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]")
# "12号"式(无月份)。排除 "9月12号" 里的 "12号" —— 前面紧跟"月"说明已被上一条吃掉
_RE_DAYNUM = re.compile(r"(?<![月\d])(\d{1,2})\s*[日号]")
# "一点"在"有一点事""差一点"里不是时间 —— 这些前缀之后的不算
_CLOCK_STOP_PREFIXES = ("有", "差", "这么", "那么", "哪", "好", "几")
_RE_FUZZY_DEADLINE = re.compile(r"(月底|月初|周末前|这周内|本周内|下周内|这两天|这几天|尽快|马上|立刻|回头|晚点|稍后)")
# 时间"未定"的表达: 说明这是件待定的事,但还没定下来 —— 与"具体时间"区别对待
_RE_TIME_QUESTION = re.compile(r"(几点|什么时候|啥时候|多久|多会儿|什么时间)")


def _cn_number(raw: str) -> int | None:
    """中文数字 → 整数(只处理时间场景需要的 0~59)。

    "七"→7、"十"→10、"十一"→11、"两"→2、"二十"→20、"三十"→30。
    复杂的数字(一百二十三)在这类场景里不会出现,不做支持 —— 与其写个
    半吊子的通用解析器,不如明确边界,遇到不认识的就返回 None。
    """
    text = (raw or "").strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    digits = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9}
    if text in digits:
        return digits[text]
    if text == "十":
        return 10
    if text.startswith("十"):        # 十一 ~ 十九
        rest = text[1:]
        return 10 + digits.get(rest, 0) if rest in digits else None
    if "十" in text:                 # 二十 / 二十五 / 三十
        head, _, tail = text.partition("十")
        if head not in digits:
            return None
        value = digits[head] * 10
        if tail:
            if tail not in digits:
                return None
            value += digits[tail]
        return value
    return None


def _tz():
    """本机时区(QQ 场景就是号主所在时区;用它算"离现在还有多久")。"""
    return datetime.now().astimezone().tzinfo


def _resolve_date(
    now: datetime,
    *,
    day_offset: int | None = None,
    weekday: int | None = None,
    weekday_next: bool = False,
    month: int | None = None,
    day: int | None = None,
    year: int | None = None,
    daynum: int | None = None,
) -> datetime | None:
    """把各种"哪一天"的表达解析成具体日期(时间部分为 0:00)。"""
    base = now.replace(hour=0, minute=0, second=0, microsecond=0)

    if day_offset is not None:
        return base + timedelta(days=day_offset)

    if month is not None and day is not None:
        year = year or base.year
        try:
            candidate = base.replace(year=year, month=month, day=day)
        except ValueError:
            return None
        # 只写了月/日,而那天已经过去 → 默认指明年(如 12月说的"1月3日")
        if year == base.year and candidate.date() < base.date():
            try:
                candidate = candidate.replace(year=year + 1)
            except ValueError:
                return None
        return candidate

    if daynum is not None:
        try:
            candidate = base.replace(day=daynum)
        except ValueError:
            return None
        if candidate.date() < base.date():
            # 本月这天已过 → 下个月的这天
            month = base.month + 1
            year = base.year + (1 if month > 12 else 0)
            month = 1 if month > 12 else month
            try:
                candidate = base.replace(year=year, month=month, day=daynum)
            except ValueError:
                return None
        return candidate

    if weekday is not None:
        delta = (weekday - base.weekday()) % 7
        if weekday_next:
            delta = delta + 7 if delta == 0 else delta
        return base + timedelta(days=delta)

    return None


def _bare_clock_hour(hour: int, period: str) -> int:
    """没有时段词时,对凌晨/清晨小时的默认读法。

    中文口语里"三点见""五点开会"几乎都指下午 —— 凌晨 3 点的事会说"凌晨三点"。
    所以 1~6 点且没写时段时按下午理解;写了时段(早上/凌晨/晚上)就一律照旧。
    这条规则只影响"裸时刻",有日期+时段修饰的表达不受影响。
    """
    if not period and 1 <= hour <= 6:
        return hour + 12
    return hour


def _apply_clock(day: datetime, hour: int | None, minute: int, period: str) -> datetime:
    """把时刻与时段作用到某一天上(下午三点 → 15:00)。"""
    if hour is None:
        # 只有时段(如"明天上午")→ 用该时段的代表时刻
        hour = {"dawn": 5, "morning": 9, "noon": 12, "afternoon": 15, "evening": 20}.get(period, 9)
    if period in ("afternoon", "evening") and hour < 12:
        hour += 12
    if period == "dawn" and hour == 12:
        hour = 0
    if hour > 23:
        hour = hour % 24
    return day.replace(hour=hour, minute=minute)


def parse_times(text: str, *, now: datetime | None = None) -> list[dict[str, Any]]:
    """解析文本里的时间表达,返回按出现位置排序的列表。

    每条形如:
      {"raw": "明天下午四点", "text": "明天下午四点", "pos": 3,
       "date": "2026-09-12", "clock": "16:00", "period": "afternoon",
       "resolved": True, "at": <UTC ISO 字符串>, "hours": 28.5,
       "deadline": False, "question": False}

    `resolved` 为 False 表示识别到"时间意图"但没解析出具体时刻
    (如"回头""月底")—— 这类同样是有效信号,只是不能用来算紧迫度。
    """
    content = normalize(text)
    if not content:
        return []
    now = now or datetime.now(_tz())
    if now.tzinfo is None:
        now = now.replace(tzinfo=_tz())

    hits: list[dict[str, Any]] = []

    def _record(
        *,
        raw: str,
        pos: int,
        day: datetime | None,
        hour: int | None,
        minute: int,
        period: str,
        deadline: bool,
    ) -> None:
        resolved = day is not None
        at_iso = ""
        hours: float | None = None
        clock = ""
        if resolved:
            moment = _apply_clock(day, _bare_clock_hour(hour, period) if hour is not None else None, minute, period)
            if hour is None and period == "":
                # 只知道哪一天、不知道几点: 用当天中午估算"还有多久"
                moment = moment.replace(hour=12)
            hours = round((moment - now).total_seconds() / 3600, 1)
            at_iso = moment.astimezone(timezone.utc).isoformat()
            clock = f"{moment.hour:02d}:{moment.minute:02d}" if (hour is not None or period) else ""
        hits.append(
            {
                "raw": raw,
                "pos": pos,
                "date": day.strftime("%Y-%m-%d") if day else "",
                "clock": clock,
                # 是否解析出了**明确时刻**。"今晚"这种只有日+时段的没有明确时刻
                # —— 合并逻辑靠这个区分"可以配对一个时刻"与"已经是一个时刻"。
                "has_clock": hour is not None,
                "period": period,
                "resolved": resolved,
                "at": at_iso,
                "hours": hours,
                "deadline": deadline,
                "question": False,
            }
        )

    # ---- 1. 绝对日期(9月12日 / 2026年9月12号) ----
    for match in _RE_DATE_ABS.finditer(content):
        year = int(match.group(1)) if match.group(1) else None
        day = _resolve_date(now, year=year, month=int(match.group(2)), day=int(match.group(3)))
        _record(raw=match.group(0), pos=match.start(), day=day, hour=None, minute=0, period="", deadline=False)

    # ---- 2. "12号"式(无月份) ----
    for match in _RE_DAYNUM.finditer(content):
        day = _resolve_date(now, daynum=int(match.group(1)))
        _record(raw=match.group(0), pos=match.start(), day=day, hour=None, minute=0, period="", deadline=False)

    # ---- 3. 星期(这周五 / 下周三 / 周日) ----
    for match in _RE_WEEKDAY.finditer(content):
        prefix = match.group(1) or ""
        weekday = _WEEKDAY_CHARS.get(match.group(2))
        if weekday is None:
            continue
        day = _resolve_date(now, weekday=weekday, weekday_next=prefix.startswith("下"))
        _record(raw=match.group(0), pos=match.start(), day=day, hour=None, minute=0, period="", deadline=False)

    # ---- 4. 相对日(今天/明天/后天/昨天) ----
    # 收集全部命中后再去重叠: "大后天"里也含"后天",不去重会算成两天。
    day_hits: list[tuple[int, str, int]] = []
    for word, offset in _DAY_WORDS.items():
        start = 0
        while True:
            index = content.find(word, start)
            if index < 0:
                break
            day_hits.append((index, word, offset))
            start = index + len(word)
    day_hits.sort(key=lambda item: (item[0], -len(item[1])))
    taken: list[tuple[int, int]] = []      # 已占用的 [起, 止) 区间
    for index, word, offset in day_hits:
        span = (index, index + len(word))
        if any(span[0] < end and start < span[1] for start, end in taken):
            continue                        # 已被更长的词覆盖(如"大后天"里的"后天")
        taken.append(span)
        day = _resolve_date(now, day_offset=offset)
        _record(raw=word, pos=index, day=day, hour=None, minute=0, period="", deadline=False)

    # ---- 4b. 日+时段连写(今晚/明早) ----
    for word, (offset, period) in _DAY_PERIOD_WORDS.items():
        index = content.find(word)
        if index < 0:
            continue
        day = _resolve_date(now, day_offset=offset)
        _record(raw=word, pos=index, day=day, hour=None, minute=0, period=period, deadline=False)

    # ---- 5. 时刻(19:30 / 七点半 / 9点) ----
    clock_marks: list[tuple[int, int, int, str]] = []
    for match in _RE_CLOCK.finditer(content):
        clock_marks.append((match.start(), int(match.group(1)), int(match.group(2)), match.group(0)))
    for match in _RE_CLOCK_CN.finditer(content):
        hour = _cn_number(match.group(1))
        if hour is None:
            continue
        # "有一点事""差一点"里的"一点"不是时间
        if any(content[max(0, match.start() - 2) : match.start()].endswith(p) for p in _CLOCK_STOP_PREFIXES):
            continue
        tail = (match.group(2) or "").strip()
        minute = 0
        if tail == "半":
            minute = 30
        elif tail == "一刻":
            minute = 15
        elif tail == "三刻":
            minute = 45
        elif tail:
            minute = _cn_number(re.sub(r"分", "", tail)) or 0
            if minute > 59:
                minute = 0
        if hour > 24:
            continue
        clock_marks.append((match.start(), hour, minute, match.group(0)))

    # ---- 6. 把日期与时刻配到一起 ----
    # 组合规则: 时刻与日期挨得够近才算**同一次**表达 ——
    # "明天下午三点见"是一个时间;而"明天要交材料,我三点在开会"是两个。
    # 窗口取 12 个字: 中文里"今晚还来不来?我七点"这种夹着问句的写法很常见,
    # 窗口太小会把明明同一次表达的时间拆成两个。
    for pos, hour, minute, raw_clock in sorted(clock_marks):
        best: dict[str, Any] | None = None
        best_distance = 13
        for item in hits:
            # 只与"有日期、还没有明确时刻"的条目配对
            if item.get("has_clock") or item["date"] == "":
                continue
            distance = abs(item["pos"] - pos)
            if distance < best_distance:
                best = item
                best_distance = distance
        period = ""
        for word, name in _PERIODS.items():
            index = content.rfind(word, max(0, pos - 6), pos + len(raw_clock) + 1)
            if index >= 0:
                period = name
                break
        if best is not None:
            if not period:
                # 日期本身带时段("今晚七点"里的"今晚")时沿用它的
                period = str(best.get("period") or "")
            day = datetime.strptime(best["date"], "%Y-%m-%d").replace(tzinfo=_tz())
            # 配到一次就吃掉那个纯日期条目 —— 否则"明天三点"在结果里会出现两条
            hits.remove(best)
            _record(raw=f"{best['raw']}{raw_clock}", pos=min(best["pos"], pos), day=day,
                    hour=hour, minute=minute, period=period, deadline=best["deadline"])
        else:
            # ---- 裸时刻(没跟任何日期配对)的消歧 ----
            # 两条启发式,都只在"确实有上下文依据"时才生效:
            #   ① 同句里已经出现过晚上/下午的时段 → 这个裸时刻大概率也是那个时段
            #      ("今晚七点…现在改到八点半" = 晚上八点半,不是次日早上);
            #   ② 同句里已有一个解析到**今天**的时间 → 这个裸时刻也按今天算
            #      (否则刚说过的"今晚"会被后面的时刻甩到明天去)。
            # 最后交给 _bare_clock_hour 处理"三点见 = 下午三点"这类默认读法。
            sibling_periods = {str(item.get("period") or "") for item in hits}
            if not period and hour < 12 and ({"evening", "afternoon"} & sibling_periods):
                period = "evening"
            today_str = now.strftime("%Y-%m-%d")
            same_day_seen = any(str(item.get("date") or "") == today_str for item in hits)
            effective_hour = _bare_clock_hour(hour, period)
            day = now.replace(hour=0, minute=0, second=0, microsecond=0)
            moment = _apply_clock(day, effective_hour, minute, period)
            if moment < now - timedelta(minutes=30) and not same_day_seen:
                moment = moment + timedelta(days=1)
            _record(raw=raw_clock, pos=pos, day=moment.replace(hour=0, minute=0), hour=moment.hour,
                    minute=minute, period=period, deadline=False)

    # 被时刻"吃掉"的纯日期条目已在合并时移除(见上一步的 hits.remove)

    # ---- 7. 模糊期限(月底/尽快/这两天) ----
    for match in _RE_FUZZY_DEADLINE.finditer(content):
        _record(raw=match.group(0), pos=match.start(), day=None, hour=None, minute=0, period="",
                deadline=True)

    # ---- 8. 期限标记(截止/之前/ddl) ----
    lowered = content.lower()
    deadline_hit = False
    for word in _DEADLINE_WORDS:
        if word in lowered:
            deadline_hit = True
            break
    if deadline_hit:
        for item in hits:
            item["deadline"] = True

    # ---- 9. "几点/什么时候"(时间意图但未定) ----
    for match in _RE_TIME_QUESTION.finditer(content):
        hits.append(
            {
                "raw": match.group(0),
                "pos": match.start(),
                "date": "",
                "clock": "",
                "period": "",
                "resolved": False,
                "at": "",
                "hours": None,
                "deadline": False,
                "question": True,
            }
        )

    hits.sort(key=lambda item: item["pos"])
    return hits


# ---------------------------------------------------------------------------
# 动作 / 语义词表
# ---------------------------------------------------------------------------
# 词表刻意"宽进": 提取阶段多给信号,判断阶段再收紧。
# 每条都用 (词, 类别) 的形式,便于把命中词原样写进证据给人看。
CHANGE_WORDS = (
    "改期", "改到", "改成", "改为", "改一下", "改了", "改了时间", "改时间",
    "换个时间", "换到", "换成", "换个", "调一下", "调整", "挪到", "变更", "变动",
    "推迟", "延后", "顺延", "提前到", "提前", "取消", "取消了", "不开了",
    "不去了", "去不了", "来不了", "不来了", "不能去了", "有变动", "有变化",
)
# "决定不变"类: 也是事件(事情定了),只是不紧急
SETTLED_WORDS = ("不改了", "不用改了", "不变了", "照旧", "按原计划", "还是原来", "继续")
AFFAIR_WORDS = (
    "组会", "例会", "开会", "会议", "班会", "面试", "笔试", "考试", "期末", "四六级",
    "上课", "补课", "课表", "答辩", "体检", "报名", "缴费", "交材料", "提交", "材料",
    "签到", "打卡", "军训", "返校", "开学", "放假", "比赛", "培训", "讲座", "汇报",
    "报告", "交作业", "收作业", "聚餐", "团建",
)
MONEY_WORDS = (
    "钱", "转账", "付款", "报销", "押金", "欠", "还款", "还钱", "借钱", "红包",
    "打钱", "收款", "付费", "费用", "学费", "房租", "工资", "尾款", "定金",
)
REQUEST_WORDS = (
    "帮忙", "帮个忙", "帮我", "帮下", "求助", "麻烦", "拜托", "救急",
    "能不能", "可不可以", "可以吗", "行不行", "行吗", "好吗", "方便吗", "有空",
    "有时间", "请", "帮",
)
INVITE_WORDS = (
    "约", "一起", "来不来", "去不去", "要不要", "来吗", "去吗", "聚", "聚餐",
    "吃饭", "打球", "开黑", "组队", "看电影", "玩", "出来",
)
URGENCY_WORDS = ("急", "尽快", "马上", "立刻", "赶紧", "抓紧", "催", "紧急", "速", "最后")
COMMITMENT_WORDS = ("务必", "记得", "别忘了", "别忘", "一定要", "必须", "答应", "保证")
SUPPRESS_WORDS = (
    "没事了", "不用了", "不用管", "无需", "已解决", "搞定了", "不用回", "看到就行",
    "随意", "随便吧", "不着急", "不急", "无所谓", "没关系",
)
# 疑问/请求语气(旧规则沿用): 保持 "pattern:xxx" 形式,便于通知里展示命中原因
QUESTION_WORDS = (
    "吗", "呢", "?", "？", "几点", "什么时候", "怎么", "为什么", "哪",
    "是不是", "有没有", "多少",
)
# 否定前缀: 命中词前面跟这些字时,语义反转(如"不改了""没取消")
_NEGATIVE_PREFIXES = ("不", "没", "别", "勿", "无")

_RE_AMOUNT = re.compile(r"(\d+(?:\.\d+)?)\s*(元|块|万|千|百|k|K|w|W|块钱)")


def _find_words(content: str, words: tuple[str, ...]) -> list[dict[str, Any]]:
    """在文本里找词表命中,返回 [{word, pos}](按位置排序,同一词只记一次)。"""
    found: list[dict[str, Any]] = []
    for word in words:
        index = content.find(word)
        if index >= 0:
            found.append({"word": word, "pos": index})
    found.sort(key=lambda item: item["pos"])
    return found


def _negated(content: str, pos: int) -> bool:
    """命中词前面 2 个字内是否有否定词。"""
    window = content[max(0, pos - 2) : pos]
    return any(prefix in window for prefix in _NEGATIVE_PREFIXES)


def extract_actions(text: str) -> dict[str, Any]:
    """词表信号: 变更 / 事务 / 金钱 / 请求 / 邀约 / 紧急 / 承诺 / 抑制 / 疑问。

    否定处理: "不改了"不该被算作"要改" —— 命中词前有否定字时单独归类,
    交给判断层决定(它通常意味着"事情定了",仍是事件但优先级低)。
    """
    content = normalize(text)
    result: dict[str, Any] = {
        "change": [],        # 真的在改
        "settled": [],       # 明确说"不改/照旧"
        "affair": [],
        "money": [],
        "request": [],
        "invite": [],
        "urgency": [],
        "commitment": [],
        "suppress": [],
        "question": [],
    }
    if not content:
        return result

    for hit in _find_words(content, CHANGE_WORDS):
        if _negated(content, hit["pos"]):
            result["settled"].append(hit)
        else:
            result["change"].append(hit)
    for word in SETTLED_WORDS:
        index = content.find(word)
        if index >= 0:
            result["settled"].append({"word": word, "pos": index})

    for key, words in (
        ("affair", AFFAIR_WORDS),
        ("money", MONEY_WORDS),
        ("invite", INVITE_WORDS),
        ("urgency", URGENCY_WORDS),
        ("commitment", COMMITMENT_WORDS),
        ("suppress", SUPPRESS_WORDS),
    ):
        result[key] = _find_words(content, words)

    for hit in _find_words(content, REQUEST_WORDS):
        if not _negated(content, hit["pos"]):
            result["request"].append(hit)

    for hit in _find_words(content, QUESTION_WORDS):
        result["question"].append(hit)

    return result


def extract_amounts(text: str) -> list[dict[str, Any]]:
    """金额线索("50块""3.5万")。"""
    content = normalize(text)
    return [
        {"raw": match.group(0), "pos": match.start(), "value": match.group(1), "unit": match.group(2)}
        for match in _RE_AMOUNT.finditer(content)
    ]


# ---------------------------------------------------------------------------
# 汇总入口
# ---------------------------------------------------------------------------
def extract_all(text: str, *, now: datetime | None = None) -> dict[str, Any]:
    """一条消息 → 全部信号(第一段的产出)。"""
    content = normalize(text)
    return {
        "text": content,
        "times": parse_times(content, now=now),
        "actions": extract_actions(content),
        "amounts": extract_amounts(content),
        "shape": shape(content),
    }


def nearest_time(signals: dict[str, Any]) -> dict[str, Any] | None:
    """离现在最近、且能算出行程的那次时间(判断紧迫度用)。"""
    times = [item for item in (signals.get("times") or []) if item.get("resolved") and item.get("hours") is not None]
    if not times:
        return None
    return min(times, key=lambda item: abs(item["hours"]))


def has_time_intent(signals: dict[str, Any]) -> bool:
    """有没有任何时间意图(含未解析的"月底""几点")。

    "改"+"时间意图" 才是排期变更;只有"改"可能只是"改天再说"。
    """
    times = signals.get("times") or []
    return bool(times)
