from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final


TIME_RANGE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?P<start_period>今天|明天|后天|上午|中午|下午|晚上|今晚)?"
    r"(?P<start_hour>\d{1,2}):(?P<start_minute>\d{2})"
    r"\s*[-~到至]\s*"
    r"(?P<end_period>上午|中午|下午|晚上|今晚)?"
    r"(?P<end_hour>\d{1,2}):(?P<end_minute>\d{2})"
)

SINGLE_TIME_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?P<period>今天|明天|后天|上午|中午|下午|晚上|今晚)?"
    r"(?P<hour>\d{1,2}):(?P<minute>\d{2})"
)

DATE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?:(?P<year>\d{4})年)?(?P<month>\d{1,2})月(?P<day>\d{1,2})日"
)

BRACKET_TITLE_PATTERN: Final[re.Pattern[str]] = re.compile(r"[【\[](.*?)[】\]]")
LOCATION_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?:地点[：:\s]*|在)"
    r"(?P<location>[A-Za-z0-9\u4e00-\u9fff\-#()（）]+?)"
    r"(?=举办|举行|召开|开展|现场|,|，|。|\.|$)"
)


@dataclass
class ScheduleCandidate:
    # 这是 ScheduleAgent 使用的标准日程候选对象。
    # 提取器只负责生成它，后面的落库和回复都围绕这份结构展开。
    title: str
    start_time: str | None
    end_time: str | None
    location: str | None
    content: str
    participants: str | None
    confidence: str


class ScheduleExtractor:
    def extract(self, text: str, now: datetime | None = None) -> ScheduleCandidate:
        reference = now or datetime.now()
        # 先统一清洗文本，再依次提取日期、时间、地点和主题。
        normalized_text = self._normalize_text(text)
        event_date = self._extract_date(normalized_text, reference)
        start_dt, end_dt = self._extract_times(normalized_text, event_date)
        location = self._extract_location(normalized_text)
        title = self._extract_title(normalized_text)
        participants = self._extract_participants(normalized_text)
        confidence = "high" if start_dt else "medium"

        return ScheduleCandidate(
            title=title,
            start_time=self._format_dt(start_dt),
            end_time=self._format_dt(end_dt),
            location=location,
            content=normalized_text,
            participants=participants,
            confidence=confidence,
        )

    def _normalize_text(self, text: str) -> str:
        # 先压平空白符，减少后面正则提取的不稳定性。
        return re.sub(r"\s+", " ", text or "").strip()

    def _extract_date(self, text: str, reference: datetime) -> datetime:
        # 优先处理相对日期，再看是否有明确年月日。
        if "明天" in text:
            return reference + timedelta(days=1)
        if "后天" in text:
            return reference + timedelta(days=2)

        date_match = DATE_PATTERN.search(text)
        if date_match:
            year = int(date_match.group("year") or reference.year)
            month = int(date_match.group("month"))
            day = int(date_match.group("day"))
            return reference.replace(year=year, month=month, day=day)

        return reference

    def _extract_times(self, text: str, event_date: datetime) -> tuple[datetime | None, datetime | None]:
        # 先尝试提取时间范围；如果没有，再退化成单个开始时间。
        range_match = TIME_RANGE_PATTERN.search(text)
        if range_match:
            start_dt = self._build_datetime(
                event_date,
                int(range_match.group("start_hour")),
                int(range_match.group("start_minute")),
                range_match.group("start_period"),
            )
            end_dt = self._build_datetime(
                event_date,
                int(range_match.group("end_hour")),
                int(range_match.group("end_minute")),
                range_match.group("end_period") or range_match.group("start_period"),
            )
            return start_dt, end_dt

        single_match = SINGLE_TIME_PATTERN.search(text)
        if single_match:
            start_dt = self._build_datetime(
                event_date,
                int(single_match.group("hour")),
                int(single_match.group("minute")),
                single_match.group("period"),
            )
            return start_dt, None

        return None, None

    def _build_datetime(self, base_date: datetime, hour: int, minute: int, period: str | None) -> datetime:
        # 根据“下午/晚上/中午”等时间段词，修正 12 小时制输入。
        normalized_hour = hour
        if period in {"下午", "晚上", "今晚"} and hour < 12:
            normalized_hour += 12
        if period == "中午" and hour < 11:
            normalized_hour += 12
        return base_date.replace(hour=normalized_hour, minute=minute, second=0, microsecond=0)

    def _extract_location(self, text: str) -> str | None:
        location_match = LOCATION_PATTERN.search(text)
        if location_match:
            return location_match.group("location").strip("，。,. ")
        return None

    def _extract_title(self, text: str) -> str:
        # 标题优先取书名号/方括号里的主题，其次退化为第一句。
        bracket_match = BRACKET_TITLE_PATTERN.search(text)
        if bracket_match:
            return bracket_match.group(1).strip()

        for separator in ("，", "。", ",", "."):
            if separator in text:
                candidate = text.split(separator, 1)[0].strip()
                if candidate:
                    return candidate[:80]

        return text[:80]

    def _extract_participants(self, text: str) -> str | None:
        participant_patterns = [
            r"欢迎(?P<participants>[^，。]+)",
            r"邀请了(?P<participants>[^，。]+)",
            r"各位同学",
        ]
        for pattern in participant_patterns:
            match = re.search(pattern, text)
            if match:
                if "participants" in match.groupdict():
                    return match.group("participants").strip()
                return "各位同学"
        return None

    def _format_dt(self, value: datetime | None) -> str | None:
        if value is None:
            return None
        return value.strftime("%Y-%m-%d %H:%M:%S")
