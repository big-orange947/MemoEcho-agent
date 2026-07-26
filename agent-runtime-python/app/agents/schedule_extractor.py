from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Final


PERIOD_TOKEN: Final[str] = r"今天|明天|后天|大后天|上午|早上|中午|下午|傍晚|晚上|今晚"
NUMBER_TOKEN: Final[str] = r"\d{1,2}|[零〇一二两三四五六七八九十]{1,3}"
TIME_TOKEN: Final[str] = rf"(?:{NUMBER_TOKEN})(?::\d{{1,2}}|点(?:半|(?:{NUMBER_TOKEN})分?)?)"

TIME_RANGE_PATTERN: Final[re.Pattern[str]] = re.compile(
    rf"(?P<start_period>{PERIOD_TOKEN})?\s*"
    rf"(?P<start_time>{TIME_TOKEN})"
    rf"\s*[-~～—到至]\s*"
    rf"(?P<end_period>{PERIOD_TOKEN})?\s*"
    rf"(?P<end_time>{TIME_TOKEN})"
)
SINGLE_TIME_PATTERN: Final[re.Pattern[str]] = re.compile(
    rf"(?P<period>{PERIOD_TOKEN})?\s*(?P<time>{TIME_TOKEN})"
)
CHINESE_DATE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?:(?P<year>\d{4})年)?(?P<month>\d{1,2})月(?P<day>\d{1,2})[日号]?"
)
NUMERIC_DATE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?:(?P<year>\d{4})[-/.])(?P<month>\d{1,2})[-/.](?P<day>\d{1,2})"
)
RELATIVE_DAYS_PATTERN: Final[re.Pattern[str]] = re.compile(r"(?P<days>\d{1,3})天后")
WEEKDAY_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?P<prefix>下下|下|本|这)?(?:周|星期|礼拜)(?P<weekday>[一二三四五六日天])"
)
BRACKET_TITLE_PATTERN: Final[re.Pattern[str]] = re.compile(r"[【\[](.*?)[】\]]")
LOCATION_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?:地点[：:\s]*|在)"
    r"(?P<location>[A-Za-z0-9\u4e00-\u9fff\-#()（）]+?)"
    r"(?=举办|举行|召开|开展|开(?:项目)?(?:例会|会议|会)|进行|参加|参与|集合|见面|现场|,|，|。|\.|$)"
)
AMBIGUOUS_DATE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"明后天|今明两天|这两天|过几天|改天|"
    r"(?:今天|明天|后天|大后天)\s*(?:或|或者|还是|/|、)\s*(?:今天|明天|后天|大后天)|"
    r"(?:周|星期|礼拜)[一二三四五六日天]\s*(?:或|或者|还是|/|、)\s*(?:(?:周|星期|礼拜))?[一二三四五六日天]"
)


@dataclass
class ScheduleCandidate:
    """保存经过本地规则归一化的标准日程候选。"""

    title: str
    start_time: str | None
    end_time: str | None
    location: str | None
    content: str
    participants: str | None
    confidence: str
    evidence: list[str] = field(default_factory=list)
    date_is_explicit: bool = False
    time_is_explicit: bool = False
    ambiguous: bool = False


class ScheduleExtractor:
    """负责可确定时间表达式的本地解析，并为 LLM 抽取提供可靠降级结果。"""

    def extract(self, text: str, now: datetime | None = None) -> ScheduleCandidate:
        # 这个函数的作用是清洗消息并依次提取日期、时间、地点、标题和参与人，同时保留证据与歧义状态。
        reference = now or datetime.now()
        normalized_text = self._normalize_text(text)
        event_date, date_evidence, date_is_explicit, date_ambiguous = self._extract_date(
            normalized_text,
            reference,
        )
        start_dt, end_dt, time_evidence, time_ambiguous = self._extract_times(
            normalized_text,
            event_date,
        )
        location = self._extract_location(normalized_text)
        title = self._extract_title(normalized_text)
        participants = self._extract_participants(normalized_text)
        evidence = [item for item in (date_evidence, time_evidence) if item]
        if location:
            evidence.append(location)
        ambiguous = date_ambiguous or time_ambiguous
        confidence = "high" if start_dt and evidence and not ambiguous else "medium"

        return ScheduleCandidate(
            title=title,
            start_time=self._format_dt(start_dt),
            end_time=self._format_dt(end_dt),
            location=location,
            content=normalized_text,
            participants=participants,
            confidence=confidence,
            evidence=evidence,
            date_is_explicit=date_is_explicit,
            time_is_explicit=bool(time_evidence),
            ambiguous=ambiguous,
        )

    @staticmethod
    def _normalize_text(text: str) -> str:
        # 这个函数的作用是压平空白并统一常见全角时间分隔符，降低平台格式差异造成的解析波动。
        normalized = re.sub(r"\s+", " ", text or "").strip()
        return normalized.replace("：", ":")

    def _extract_date(self, text: str, reference: datetime) -> tuple[datetime, str, bool, bool]:
        # 这个函数的作用是把明确日期、相对日期和星期表达式归一化为参考时区内的具体日期。
        numeric_matches = list(NUMERIC_DATE_PATTERN.finditer(text))
        chinese_matches = list(CHINESE_DATE_PATTERN.finditer(text))
        explicit_matches = sorted(
            [*numeric_matches, *chinese_matches],
            key=lambda match: match.start(),
        )
        if explicit_matches:
            # 显式年月日比“今天发布、明天转发”等叙述时间更可靠，必须优先作为事件日期。
            match = explicit_matches[0]
            year_text = match.groupdict().get("year")
            result = self._build_explicit_date(
                reference,
                int(year_text or reference.year),
                int(match.group("month")),
                int(match.group("day")),
                match.group(0),
            )
            if len(explicit_matches) > 1:
                return result[0], result[1], result[2], True
            return result

        if AMBIGUOUS_DATE_PATTERN.search(text):
            # “明天或后天”没有唯一日期，保留参考日仅用于继续抽取，最终候选必须进入澄清状态。
            return reference, AMBIGUOUS_DATE_PATTERN.search(text).group(0), True, True

        relative_days = {
            "大后天": 3,
            "后天": 2,
            "明天": 1,
            "今天": 0,
            "今晚": 0,
        }
        for phrase, days in relative_days.items():
            if phrase in text:
                return reference + timedelta(days=days), phrase, True, False

        relative_match = RELATIVE_DAYS_PATTERN.search(text)
        if relative_match:
            days = int(relative_match.group("days"))
            return reference + timedelta(days=days), relative_match.group(0), True, False

        weekday_match = WEEKDAY_PATTERN.search(text)
        if weekday_match:
            target_weekday = "一二三四五六日天".index(weekday_match.group("weekday"))
            if target_weekday == 7:
                target_weekday = 6
            prefix = weekday_match.group("prefix") or ""
            base_offset = {"下下": 14, "下": 7, "本": 0, "这": 0}.get(prefix, 0)
            current_week_start = reference - timedelta(days=reference.weekday())
            event_date = current_week_start + timedelta(days=base_offset + target_weekday)
            if not prefix and event_date.date() < reference.date():
                event_date += timedelta(days=7)
            return event_date, weekday_match.group(0), True, False

        if "月底" in text:
            last_day = calendar.monthrange(reference.year, reference.month)[1]
            return reference.replace(day=last_day), "月底", True, False

        return reference, "", False, False

    @staticmethod
    def _build_explicit_date(
        reference: datetime,
        year: int,
        month: int,
        day: int,
        evidence: str,
    ) -> tuple[datetime, str, bool, bool]:
        # 这个函数的作用是校验显式年月日；非法日期保留参考时间并标记为歧义，禁止后续直接落库。
        try:
            event_date = reference.replace(year=year, month=month, day=day)
        except ValueError:
            return reference, evidence, True, True
        return event_date, evidence, True, False

    def _extract_times(
        self,
        text: str,
        event_date: datetime,
    ) -> tuple[datetime | None, datetime | None, str, bool]:
        # 这个函数的作用是优先解析时间范围，再解析单个时间，并识别不合理的结束时间。
        range_match = TIME_RANGE_PATTERN.search(text)
        if range_match:
            start_clock = self._parse_clock(range_match.group("start_time"))
            end_clock = self._parse_clock(range_match.group("end_time"))
            if start_clock is None or end_clock is None:
                return None, None, range_match.group(0), True
            start_dt = self._build_datetime(
                event_date,
                start_clock[0],
                start_clock[1],
                range_match.group("start_period"),
            )
            end_dt = self._build_datetime(
                event_date,
                end_clock[0],
                end_clock[1],
                range_match.group("end_period") or range_match.group("start_period"),
            )
            if end_dt <= start_dt:
                if start_dt.hour >= 18 and end_dt.hour <= 6:
                    end_dt += timedelta(days=1)
                else:
                    return start_dt, end_dt, range_match.group(0), True
            return start_dt, end_dt, range_match.group(0), False

        single_match = SINGLE_TIME_PATTERN.search(text)
        if single_match:
            clock = self._parse_clock(single_match.group("time"))
            if clock is None:
                return None, None, single_match.group(0), True
            start_dt = self._build_datetime(
                event_date,
                clock[0],
                clock[1],
                single_match.group("period"),
            )
            remaining_text = f"{text[:single_match.start()]} {text[single_match.end():]}"
            has_second_time = SINGLE_TIME_PATTERN.search(remaining_text) is not None
            return start_dt, None, single_match.group(0), has_second_time

        return None, None, "", False

    def _parse_clock(self, value: str) -> tuple[int, int] | None:
        # 这个函数的作用是兼容 14:30、下午三点、三点半和三点十五分等常见中文时间写法。
        normalized = value.strip()
        if ":" in normalized:
            hour_text, minute_text = normalized.split(":", 1)
            hour = self._parse_number(hour_text)
            minute = self._parse_number(minute_text)
        else:
            hour_text, minute_text = normalized.split("点", 1)
            hour = self._parse_number(hour_text)
            if minute_text == "半":
                minute = 30
            elif not minute_text:
                minute = 0
            else:
                minute = self._parse_number(minute_text.rstrip("分"))
        if hour is None or minute is None or not 0 <= hour <= 23 or not 0 <= minute <= 59:
            return None
        return hour, minute

    @staticmethod
    def _parse_number(value: str) -> int | None:
        # 这个函数的作用是把两位以内的阿拉伯数字或中文数字转换为整数。
        normalized = value.strip()
        if normalized.isdigit():
            return int(normalized)
        digits = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
        if normalized in digits:
            return digits[normalized]
        if "十" in normalized:
            left, right = normalized.split("十", 1)
            tens = digits.get(left, 1) if left else 1
            ones = digits.get(right, 0) if right else 0
            return tens * 10 + ones
        return None

    @staticmethod
    def _build_datetime(base_date: datetime, hour: int, minute: int, period: str | None) -> datetime:
        # 这个函数的作用是根据上午、下午、晚上等时间段词修正十二小时制输入。
        normalized_hour = hour
        if period in {"下午", "傍晚", "晚上", "今晚"} and hour < 12:
            normalized_hour += 12
        if period == "中午" and hour < 11:
            normalized_hour += 12
        return base_date.replace(hour=normalized_hour, minute=minute, second=0, microsecond=0)

    @staticmethod
    def _extract_location(text: str) -> str | None:
        # 这个函数的作用是从“地点”或“在某处举行”等表达中提取可直接展示的地点。
        location_match = LOCATION_PATTERN.search(text)
        if location_match:
            return location_match.group("location").strip("，。,. ")
        return None

    @staticmethod
    def _extract_title(text: str) -> str:
        # 这个函数的作用是优先读取括号标题，缺失时再使用首个短句作为候选标题。
        bracket_match = BRACKET_TITLE_PATTERN.search(text)
        if bracket_match:
            return bracket_match.group(1).strip()
        for separator in ("，", "。", ",", "."):
            if separator in text:
                candidate = text.split(separator, 1)[0].strip()
                if candidate:
                    return candidate[:80]
        return text[:80]

    @staticmethod
    def _extract_participants(text: str) -> str | None:
        # 这个函数的作用是提取消息中明确出现的参与对象，不根据活动类型自行推测人群。
        participant_patterns = (
            r"欢迎(?P<participants>[^，。]+)",
            r"邀请了(?P<participants>[^，。]+)",
            r"各位同学",
        )
        for pattern in participant_patterns:
            match = re.search(pattern, text)
            if match:
                if "participants" in match.groupdict():
                    return match.group("participants").strip()
                return "各位同学"
        return None

    @staticmethod
    def _format_dt(value: datetime | None) -> str | None:
        # 这个函数的作用是把内部 datetime 转成 Java 日程服务当前接受的稳定格式。
        if value is None:
            return None
        return value.strftime("%Y-%m-%d %H:%M:%S")
