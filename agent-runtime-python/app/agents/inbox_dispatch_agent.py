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
        """
        根据默认双通道规则和命中的会话通知策略，决定立即提醒、缓冲摘要或静默记录。
        """
        event = task_context.event
        policy = self._resolve_notification_policy(task_context)
        aggregation_key = f"{event.platform}:{event.chat_type}:{event.chat_id}"
        reason = self._classify_urgency(event, policy["keywords"])

        if policy["mode"] == "MUTED":
            return self._suppressed_result(task_context, aggregation_key, policy, "muted")

        if policy["mode"] == "URGENT_ONLY" and reason == "none":
            return self._suppressed_result(task_context, aggregation_key, policy, "urgent_only")

        if policy["mode"] == "DIGEST_ONLY":
            reason = "none"

        if reason != "none":
            digest_context = self._include_urgent_in_digest(event, aggregation_key, policy)
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
                    "notificationPolicy": policy["mode"],
                    "includedInDigest": digest_context["included"],
                    "bufferedCount": digest_context["bufferedCount"],
                },
                reply_draft=urgent_reply,
            )

        buffered_result = self.slow_channel_buffer.add(
            aggregation_key,
            event,
            window_seconds=policy["digest_window_seconds"],
            max_messages=policy["digest_max_messages"],
        )
        return AgentResult(
            task_id=task_context.task_id,
            agent=self.name,
            status="success",
            structured_result={
                "dispatchMode": "normal",
                "urgencyReason": "none",
                "aggregationKey": aggregation_key,
                "shouldNotifyNow": bool(buffered_result["flushed"]),
                "notificationPolicy": policy["mode"],
                **buffered_result,
            },
            reply_draft=buffered_result["summaryCandidate"] or "",
        )

    def _classify_urgency(self, event, profile_keywords: list[str]) -> str:
        """识别私聊、@ 自身以及用户定义重点关键词对应的即时提醒原因。"""
        if event.chat_type == "private":
            return "private_chat"
        if self._is_at_self(event):
            return "at_self"
        if self._contains_urgent_keyword(event.text or "", profile_keywords):
            return "keyword_notice"
        return "none"

    @staticmethod
    def _resolve_notification_policy(task_context: AgentTaskContext) -> dict[str, object]:
        """从会话设定集匹配结果中读取通知策略，缺失字段时安全回退到默认双通道配置。"""
        match = task_context.metadata.get("conversation_profile_match") or {}
        profile = match.get("profile") or {}
        mode = str(profile.get("notificationMode") or "AUTO").upper()
        if mode not in {"AUTO", "URGENT_ONLY", "DIGEST_ONLY", "MUTED"}:
            mode = "AUTO"

        keywords = profile.get("notificationKeywords") or []
        return {
            "mode": mode,
            "keywords": [str(keyword) for keyword in keywords if str(keyword).strip()],
            "digest_window_seconds": InboxDispatchAgent._positive_int(profile.get("digestWindowSeconds")),
            "digest_max_messages": InboxDispatchAgent._positive_int(profile.get("digestMaxMessages")),
            "include_urgent_in_digest": bool(profile.get("includeUrgentInDigest", False)),
        }

    def _include_urgent_in_digest(
        self,
        event,
        aggregation_key: str,
        policy: dict[str, object],
    ) -> dict[str, object]:
        """按用户选择把快通道消息加入后续摘要上下文，但禁止它单独触发阈值，以免重复打扰。"""
        if not policy["include_urgent_in_digest"]:
            return {"included": False, "bufferedCount": 0}
        result = self.slow_channel_buffer.add(
            aggregation_key,
            event,
            window_seconds=policy["digest_window_seconds"],
            max_messages=policy["digest_max_messages"],
            allow_threshold_flush=False,
        )
        return {"included": True, "bufferedCount": result["bufferedCount"]}

    def _suppressed_result(
        self,
        task_context: AgentTaskContext,
        aggregation_key: str,
        policy: dict[str, object],
        reason: str,
    ) -> AgentResult:
        """构造静默策略结果；消息仍会进入事件中心历史，但不会触发工作台提醒或自动回复。"""
        return AgentResult(
            task_id=task_context.task_id,
            agent=self.name,
            status="success",
            structured_result={
                "dispatchMode": "normal",
                "urgencyReason": reason,
                "aggregationKey": aggregation_key,
                "shouldNotifyNow": False,
                "buffered": False,
                "bufferedCount": 0,
                "flushed": False,
                "flushReason": "policy_suppressed",
                "summaryCandidate": None,
                "notificationPolicy": policy["mode"],
                "suppressedByPolicy": True,
            },
        )

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
    def _contains_urgent_keyword(text: str, profile_keywords: list[str]) -> bool:
        """匹配内置重点词和用户为当前会话额外配置的重点词。"""
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
        keywords.extend(keyword.lower() for keyword in profile_keywords)
        return any(keyword in normalized for keyword in keywords)

    @staticmethod
    def _positive_int(value: object) -> int | None:
        """将设定集中的可选数字转换为正整数，错误输入交给缓冲区默认值处理。"""
        try:
            normalized = int(value)
        except (TypeError, ValueError):
            return None
        return normalized if normalized > 0 else None

    @staticmethod
    def _build_urgent_reply(event, reason: str) -> str:
        content = (event.text or "").strip()
        if reason == "keyword_notice" and content:
            return f"检测到一条需要优先关注的消息：{content}"
        return ""
