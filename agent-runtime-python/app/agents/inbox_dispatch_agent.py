from __future__ import annotations

from app.agents.base import BaseAgent
from app.schemas.results import AgentResult
from app.schemas.tasks import AgentTaskContext
from app.services.slow_channel_buffer import SlowChannelBuffer


class InboxDispatchAgent(BaseAgent):
    name = "inbox_dispatch"

    def __init__(self, tools, slow_channel_buffer: SlowChannelBuffer) -> None:
        super().__init__(tools)
        self.slow_channel_buffer = slow_channel_buffer

    async def run(self, task_context: AgentTaskContext, action: str) -> AgentResult:
        event = task_context.event
        reason = self._classify_urgency(event)
        aggregation_key = f"{event.platform}:{event.chat_type}:{event.chat_id}"

        if reason != "none":
            urgent_reply = self._build_urgent_reply(event, reason)
            return AgentResult(
                task_id=task_context.task_id,
                agent=self.name,
                status="success",
                structured_result={
                    "dispatchMode": "urgent",
                    "urgencyReason": reason,
                    "aggregationKey": aggregation_key,
                    "shouldNotifyNow": True,
                    "buffered": False,
                    "flushed": False,
                    "summaryCandidate": None,
                },
                reply_draft=urgent_reply,
            )

        buffered_result = self.slow_channel_buffer.add(aggregation_key, event)
        return AgentResult(
            task_id=task_context.task_id,
            agent=self.name,
            status="success",
            structured_result={
                "dispatchMode": "normal",
                "urgencyReason": "none",
                "aggregationKey": aggregation_key,
                "shouldNotifyNow": bool(buffered_result["flushed"]),
                **buffered_result,
            },
            reply_draft=buffered_result["summaryCandidate"] or "",
        )

    def _classify_urgency(self, event) -> str:
        if event.chat_type == "private":
            return "private_chat"
        if self._is_at_self(event):
            return "at_self"
        if self._contains_urgent_keyword(event.text or ""):
            return "keyword_notice"
        return "none"

    def _is_at_self(self, event) -> bool:
        self_id = event.self_id or self._extract_self_id(event.raw_payload)
        if not self_id:
            return False
        if self_id in event.mentions:
            return True
        if self._has_at_segment(event.raw_payload, self_id):
            return True
        return f"[CQ:at,qq={self_id}]" in (event.text or "")

    @staticmethod
    def _extract_self_id(raw_payload: dict) -> str | None:
        if not raw_payload:
            return None
        value = raw_payload.get("self_id") or raw_payload.get("selfId")
        return str(value) if value is not None else None

    @staticmethod
    def _has_at_segment(raw_payload: dict, self_id: str) -> bool:
        if not raw_payload:
            return False
        message = raw_payload.get("message")
        if not isinstance(message, list):
            return False
        for segment in message:
            if not isinstance(segment, dict):
                continue
            if segment.get("type") != "at":
                continue
            data = segment.get("data") or {}
            if str(data.get("qq", "")) == self_id:
                return True
        return False

    @staticmethod
    def _contains_urgent_keyword(text: str) -> bool:
        normalized = text.lower()
        keywords = [
            "通知",
            "截止",
            "报名",
            "会议",
            "开会",
            "今天",
            "明天",
            "notice",
            "deadline",
            "meeting",
        ]
        return any(keyword in normalized for keyword in keywords)

    @staticmethod
    def _build_urgent_reply(event, reason: str) -> str:
        content = (event.text or "").strip()
        if reason == "keyword_notice" and content:
            return f"检测到一条需要优先关注的消息：{content}"
        return ""
