from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import re


@dataclass(slots=True)
class TaskCandidate:
    # 这是 WorkAgent 向下游传递的标准任务候选对象。
    # 把结构固定下来之后，测试和工具调用都会更稳定。
    title: str
    description: str
    due_time: str | None
    priority: str
    confidence: str
    actionable: bool


class TaskExtractor:
    TASK_KEYWORDS = (
        "todo", "task", "work", "plan", "submit", "finish", "review",
        "待办", "任务", "完成", "提交", "整理", "汇总", "处理", "跟进", "准备",
    )
    HIGH_PRIORITY_KEYWORDS = (
        "urgent", "asap", "today", "tonight", "deadline", "immediately",
        "马上", "立即", "尽快", "今天", "今晚", "截止", "ddl",
    )

    def extract(self, text: str) -> TaskCandidate:
        # 这里先走规则提取，保证在没有大模型时，任务链路也能正常工作。
        normalized = self._normalize(text)
        due_time = self._extract_due_time(normalized)
        actionable = self._is_actionable(normalized, due_time)
        title = self._build_title(normalized)
        priority = self._detect_priority(normalized, due_time)
        confidence = "high" if actionable and due_time else "medium" if actionable else "low"

        return TaskCandidate(
            title=title,
            description=normalized,
            due_time=due_time,
            priority=priority,
            confidence=confidence,
            actionable=actionable,
        )

    def _normalize(self, text: str) -> str:
        # 先清理多余空白和 CQ @ 片段，避免后面的关键词匹配被传输层语法干扰。
        cleaned = re.sub(r"\s+", " ", text or "").strip()
        cleaned = re.sub(r"\[CQ:at,qq=\d+\]", "", cleaned).strip()
        return cleaned

    def _is_actionable(self, text: str, due_time: str | None) -> bool:
        lowered = text.lower()
        if any(keyword in lowered for keyword in self.TASK_KEYWORDS):
            return True
        return due_time is not None and any(token in lowered for token in ("please", "need", "请", "帮我", "记得"))

    def _build_title(self, text: str) -> str:
        # 默认取第一句作为任务标题，完整原文仍然保留在 description 里，
        # 方便后面展示、编辑或二次理解。
        sentence = re.split(r"[,.!?\n;，。！？；]", text, maxsplit=1)[0].strip()
        sentence = re.sub(r"^(please|need to|remember to|请|帮我|记得)\s*", "", sentence, flags=re.IGNORECASE)
        if len(sentence) > 48:
            return sentence[:48].rstrip() + "..."
        return sentence or "task reminder"

    def _detect_priority(self, text: str, due_time: str | None) -> str:
        lowered = text.lower()
        if any(keyword in lowered for keyword in self.HIGH_PRIORITY_KEYWORDS):
            return "high"
        if due_time is not None:
            return "medium"
        return "normal"

    def _extract_due_time(self, text: str) -> str | None:
        # 优先提取明确时间；相对时间只做兜底，因为它依赖当前运行时钟。
        explicit_datetime = re.search(r"(20\d{2}-\d{2}-\d{2})\s+(\d{1,2}:\d{2})", text)
        if explicit_datetime:
            return f"{explicit_datetime.group(1)} {explicit_datetime.group(2)}:00"

        explicit_date = re.search(r"(20\d{2}-\d{2}-\d{2})", text)
        if explicit_date:
            return f"{explicit_date.group(1)} 18:00:00"

        return self._extract_relative_due_time(text)

    def _extract_relative_due_time(self, text: str) -> str | None:
        # 相对时间会在这里提前转成绝对时间，
        # 这样下游 Java 服务就不需要再理解自然语言时间表达。
        now = datetime.now()
        lowered = text.lower()
        hour_match = re.search(r"(\d{1,2}):(\d{2})", text)
        hour = int(hour_match.group(1)) if hour_match else 18
        minute = int(hour_match.group(2)) if hour_match else 0

        if "tomorrow" in lowered or "明天" in text:
            due = now + timedelta(days=1)
            return due.replace(hour=hour, minute=minute, second=0, microsecond=0).strftime("%Y-%m-%d %H:%M:%S")
        if "today" in lowered or "今天" in text or "今晚" in text:
            due = now
            return due.replace(hour=hour, minute=minute, second=0, microsecond=0).strftime("%Y-%m-%d %H:%M:%S")
        if "后天" in text:
            due = now + timedelta(days=2)
            return due.replace(hour=hour, minute=minute, second=0, microsecond=0).strftime("%Y-%m-%d %H:%M:%S")
        return None
