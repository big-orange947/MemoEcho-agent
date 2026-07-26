from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.clients.event_center_service import EventCenterServiceClient
from app.clients.llm_service import LlmServiceClient
from app.schemas.events import UnifiedEvent
from app.schemas.model_profiles import ResolvedUserModelProfile
from app.schemas.profiles import ConversationProfileMatchResult


logger = logging.getLogger(__name__)


class ConversationTaskCompletionService:
    """判断结构化会话任务是否已经完成，并向用户提交结束代理申请。"""

    MIN_CONFIDENCE = 0.86

    def __init__(
        self,
        event_center_client: EventCenterServiceClient,
        llm_client: LlmServiceClient,
    ) -> None:
        """注入模型和事件中心；模型只负责判断，状态由 Event Center 持久化。"""
        self.event_center_client = event_center_client
        self.llm_client = llm_client

    async def evaluate_and_request(
        self,
        event: UnifiedEvent,
        route: str,
        profile_match: ConversationProfileMatchResult | None,
        history_context: list[dict[str, Any]],
        final_reply: str,
        model_profile: ResolvedUserModelProfile | None,
    ) -> dict[str, Any] | None:
        """在回复完成后检查一次任务；任何判断异常都不能阻断正常聊天。"""
        if not self._should_evaluate(route, profile_match, model_profile):
            return None

        assert profile_match is not None and profile_match.profile is not None
        profile = profile_match.profile
        task = profile.profile_context.task
        try:
            assessment = await self._evaluate_with_model(
                event,
                task.objective,
                task.success_criteria,
                history_context,
                final_reply,
                model_profile,
            )
        except Exception as exception:
            logger.info(
                "会话任务完成度判断失败，保留进行中状态。eventId=%s, error=%s",
                event.event_id,
                type(exception).__name__,
            )
            return None

        confidence = self._safe_confidence(assessment.get("confidence"))
        completed = bool(assessment.get("completed")) and confidence >= self.MIN_CONFIDENCE
        evidence = self._normalize_evidence(assessment.get("evidence"), history_context, event)
        # 没有对方明确发言作为证据时绝不结束任务，防止 Agent 用自己的草稿证明自己成功。
        if not completed or not evidence:
            return {"requested": False, "confidence": confidence}

        summary = str(assessment.get("summary") or "会话任务已满足成功条件").strip()
        reason = str(assessment.get("reason") or "对方已明确确认任务结果").strip()
        state = await self.event_center_client.request_conversation_task_completion(
            event=event,
            profile_id=profile.id,
            summary=summary,
            reason=reason,
            evidence=evidence,
        )
        logger.info(
            "已提交结束代理申请：eventId=%s, profileId=%s, chatId=%s, confidence=%.2f",
            event.event_id,
            profile.id,
            event.chat_id,
            confidence,
        )
        return {"requested": True, "confidence": confidence, "state": state}

    def _should_evaluate(
        self,
        route: str,
        profile_match: ConversationProfileMatchResult | None,
        model_profile: ResolvedUserModelProfile | None,
    ) -> bool:
        """只评估存在明确任务、状态为 ACTIVE 且模型可用的社交代理会话。"""
        if route != "social_reply" or profile_match is None or not profile_match.active:
            return False
        profile = profile_match.profile
        if profile is None or not profile.profile_context.task.objective.strip():
            return False
        task_state = profile_match.task_state
        if task_state is not None and task_state.status.upper() != "ACTIVE":
            return False
        return self.llm_client.is_enabled(model_profile)

    async def _evaluate_with_model(
        self,
        event: UnifiedEvent,
        objective: str,
        success_criteria: list[str],
        history_context: list[dict[str, Any]],
        final_reply: str,
        model_profile: ResolvedUserModelProfile | None,
    ) -> dict[str, Any]:
        """要求模型按封闭 JSON 契约判断完成度，不允许模型自行扩大成功条件。"""
        timeline = self._format_timeline(history_context, event)
        response = await self.llm_client.generate_reply(
            system_prompt=(
                "你是 ConversationTaskCompletionEvaluator，只判断代理任务是否已经完成，不参与聊天。\n"
                "完成必须同时满足：任务目标已经达成；每条成功条件均有当前会话证据；"
                "对方已经明确接受、确认或给出最终结果。\n"
                "寒暄、暂时同意、仍在讨论、Agent 自己提出的方案、Agent 自己说完成了，都不能证明完成。\n"
                "只能把标记为‘对方’的原始发言作为完成证据；标记为‘我/代理’的消息只能用于理解过程。\n"
                "证据不足、含糊或仍需追问时必须 completed=false。不要根据常识补全事实。\n"
                "只输出 JSON，不要 Markdown："
                '{"completed":false,"confidence":0.0,"summary":"",'
                '"reason":"","evidence":["对方原话"]}'
            ),
            user_message=(
                f"任务目标：{objective}\n"
                f"成功条件：{'；'.join(success_criteria) if success_criteria else '任务目标本身明确达成'}\n"
                f"按时间排序的会话：\n{timeline}\n"
                f"本轮代理候选回复（不可作为完成证据）：{final_reply}"
            ),
            temperature=0.0,
            model_profile=model_profile,
        )
        return self._parse_json_object(response)

    @staticmethod
    def _format_timeline(history_context: list[dict[str, Any]], event: UnifiedEvent) -> str:
        """把可信时间线压缩为角色明确的文本，并补上可能尚未落库的当前消息。"""
        lines: list[str] = []
        current_event_seen = False
        for item in history_context[-30:]:
            text = " ".join(str(item.get("text") or "").split())
            if not text:
                continue
            role = "对方" if str(item.get("role") or "").lower() == "peer" else "我/代理"
            event_id = str(item.get("eventId") or "")
            if event_id and event_id == event.event_id:
                current_event_seen = True
            lines.append(f"{role}：{text[:240]}")
        current_text = " ".join(str(event.text or "").split())
        if current_text and not current_event_seen:
            lines.append(f"对方：{current_text[:240]}")
        return "\n".join(lines[-30:]) or "无可用会话证据"

    @classmethod
    def _normalize_evidence(
        cls,
        raw_evidence: Any,
        history_context: list[dict[str, Any]],
        event: UnifiedEvent,
    ) -> list[str]:
        """只保留能在对方原始消息中找到的证据，过滤模型改写和 Agent 自证。"""
        if not isinstance(raw_evidence, list):
            return []
        peer_messages = [
            " ".join(str(item.get("text") or "").split())
            for item in history_context
            if str(item.get("role") or "").lower() == "peer" and str(item.get("text") or "").strip()
        ]
        current_text = " ".join(str(event.text or "").split())
        if current_text:
            peer_messages.append(current_text)

        normalized: list[str] = []
        for item in raw_evidence[:8]:
            candidate = " ".join(str(item or "").split()).strip("‘’\" ")
            if not candidate:
                continue
            if any(candidate in message or message in candidate for message in peer_messages):
                if candidate not in normalized:
                    normalized.append(candidate[:240])
        return normalized

    @staticmethod
    def _safe_confidence(value: Any) -> float:
        """把模型置信度限制在 0 到 1，非法值按 0 处理。"""
        try:
            return min(max(float(value), 0.0), 1.0)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _parse_json_object(text: str) -> dict[str, Any]:
        """兼容模型偶尔包裹代码块的情况，但拒绝非 JSON 内容。"""
        normalized = re.sub(r"^```(?:json)?\s*|\s*```$", "", str(text or "").strip(), flags=re.IGNORECASE)
        payload = json.loads(normalized)
        if not isinstance(payload, dict):
            raise ValueError("任务完成度响应必须是 JSON 对象")
        return payload
