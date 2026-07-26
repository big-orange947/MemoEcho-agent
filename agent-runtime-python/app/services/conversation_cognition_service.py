from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

from app.clients.event_center_service import EventCenterServiceClient
from app.clients.llm_service import LlmServiceClient
from app.schemas.conversation_cognition import (
    CognitionFieldResult,
    ConversationCognitionRequest,
    ConversationCognitionResponse,
)
from app.schemas.conversation_progress import ConversationProgressMessage


logger = logging.getLogger(__name__)


class ConversationCognitionService:
    """根据双方真实聊天记录生成可校正、可追溯的会话认知卡。"""

    def __init__(
        self,
        event_center_client: EventCenterServiceClient,
        llm_client: LlmServiceClient,
    ) -> None:
        # 这个构造函数的作用是注入用户模型解析客户端和统一 LLM 客户端。
        self.event_center_client = event_center_client
        self.llm_client = llm_client

    async def analyze(
        self,
        request: ConversationCognitionRequest,
    ) -> ConversationCognitionResponse:
        """分析最新双向时间线；失败时只保留可由最后消息直接确定的当前进度。"""
        messages = self._chronological_messages(request.messages)
        source_event_ids = [item.event_id for item in messages if item.event_id][-120:]
        fallback = self._fallback_response(messages, source_event_ids)
        if not messages:
            return fallback

        try:
            resolved = await self.event_center_client.resolve_user_model_profile(
                route="chat_summary",
                user_id=request.user_id,
            )
            model_profile = resolved.profile if resolved and resolved.matched else None
            if not self.llm_client.is_enabled(model_profile):
                raise RuntimeError("chat_summary model is not configured")

            raw = await self.llm_client.generate_reply(
                system_prompt=self._build_system_prompt(),
                user_message=self._build_transcript(request, messages),
                temperature=0.1,
                model_profile=model_profile,
            )
            payload = self._parse_json_object(raw)
            return self._build_response(payload, messages, source_event_ids)
        except Exception as exception:
            # 认知卡是辅助信息。模型超时、格式错误或未配置时必须保留真实时间线，不能生成虚假人格。
            logger.warning(
                "会话认知分析失败，已使用保守结果：platform=%s, chatType=%s, chatId=%s, error=%s",
                request.platform,
                request.chat_type,
                request.chat_id,
                exception,
            )
            return fallback

    @staticmethod
    def _build_system_prompt() -> str:
        """构造严格的认知卡分析约束，要求模型把事实、观察和未知信息分开。"""
        return (
            "你是 Memo Echo 的会话认知分析器，不参与聊天，也不替用户作决定。\n"
            "只能依据给定的双向聊天记录分析，不使用常识补全身份、关系、职业、性格或背景。\n"
            "必须严格区分【对方】【我】【Agent代发】；我和Agent代发都属于账号主人一侧，"
            "但Agent代发内容不能作为账号主人表达习惯的样本。\n"
            "偶发的一句话不能上升为稳定性格。关系、称呼和表达习惯证据不足时 value 必须为空，"
            "confidence 必须低于 0.5。明确重复出现的证据才允许较高置信度。\n"
            "knownFacts 只写聊天中明确陈述且与后续交流有帮助的事实；不得推断敏感个人信息。\n"
            "currentProgress 只描述当前聊到哪一步，不给建议；openQuestions 只列尚未得到答案的问题。\n"
            "只输出一个 JSON 对象，不要 Markdown、解释或额外文字。JSON 必须包含："
            "relationship、preferredAddress、counterpartyTraits、ownerExpressionHabits、"
            "counterpartyExpressionHabits、backgroundSummary、currentProgress；"
            "这七项均为 {\"value\":字符串,\"confidence\":0到1}。"
            "还必须包含 knownFacts、recentTopics、openQuestions 三个字符串数组。"
        )

    def _build_transcript(
        self,
        request: ConversationCognitionRequest,
        messages: list[ConversationProgressMessage],
    ) -> str:
        """把消息转换成带稳定身份和事件 ID 的短时间线，避免模型颠倒聊天双方。"""
        lines = [
            f"平台：{request.platform}",
            f"会话类型：{request.chat_type}",
            "消息按时间从旧到新排列：",
        ]
        for message in messages[-80:]:
            event_id = message.event_id or "unknown"
            lines.append(
                f"[{event_id}][{self._speaker_label(message)}] {self._message_text(message)}"
            )
        return "\n".join(lines)

    @staticmethod
    def _chronological_messages(
        messages: list[ConversationProgressMessage],
    ) -> list[ConversationProgressMessage]:
        """去除空事件并按时间排序，同一事件只保留一次，防止重试消息污染推断。"""
        unique: dict[str, ConversationProgressMessage] = {}
        anonymous: list[ConversationProgressMessage] = []
        for message in messages:
            if not message.text.strip() and not message.attachments:
                continue
            if message.event_id:
                unique[message.event_id] = message
            else:
                anonymous.append(message)
        return sorted([*unique.values(), *anonymous], key=lambda item: item.timestamp or "")

    @staticmethod
    def _is_own_message(message: ConversationProgressMessage) -> bool:
        """依据 Event Center 可信来源判断账号主人一侧消息，不使用昵称猜测身份。"""
        origin = message.message_origin.strip().upper()
        return origin in {"USER_MANUAL", "AGENT_AUTO", "AGENT_CONFIRMED"} or message.sender_role == "self"

    def _speaker_label(self, message: ConversationProgressMessage) -> str:
        """为人工消息、Agent 代发和对方消息生成互斥标签。"""
        origin = message.message_origin.strip().upper()
        if origin in {"AGENT_AUTO", "AGENT_CONFIRMED"}:
            return "Agent代发"
        if self._is_own_message(message):
            return "我"
        return "对方"

    @staticmethod
    def _message_text(message: ConversationProgressMessage) -> str:
        """提取适合分析的文本；附件只作为类型事实，不猜测其内容。"""
        text = " ".join(message.text.split()).strip()
        if text:
            return text[:500]
        return f"发送了{len(message.attachments)}个附件"

    @staticmethod
    def _parse_json_object(raw: str) -> dict:
        """兼容模型偶尔附带的 JSON 代码围栏，并拒绝非对象结果。"""
        normalized = str(raw or "").strip()
        normalized = re.sub(r"^```(?:json)?\s*", "", normalized, flags=re.IGNORECASE)
        normalized = re.sub(r"\s*```$", "", normalized)
        start = normalized.find("{")
        end = normalized.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("模型没有返回 JSON 对象")
        payload = json.loads(normalized[start : end + 1])
        if not isinstance(payload, dict):
            raise ValueError("认知分析结果不是 JSON 对象")
        return payload

    def _build_response(
        self,
        payload: dict,
        messages: list[ConversationProgressMessage],
        source_event_ids: list[str],
    ) -> ConversationCognitionResponse:
        """校验并裁剪模型输出，服务端只接收有限长度的认知结论而非整段历史。"""
        return ConversationCognitionResponse(
            relationship=self._field(payload.get("relationship"), 240),
            preferredAddress=self._field(payload.get("preferredAddress"), 120),
            counterpartyTraits=self._field(payload.get("counterpartyTraits"), 500),
            ownerExpressionHabits=self._field(payload.get("ownerExpressionHabits"), 500),
            counterpartyExpressionHabits=self._field(payload.get("counterpartyExpressionHabits"), 500),
            backgroundSummary=self._field(payload.get("backgroundSummary"), 1000),
            currentProgress=self._field(payload.get("currentProgress"), 1000),
            knownFacts=self._string_list(payload.get("knownFacts"), 30, 300),
            recentTopics=self._string_list(payload.get("recentTopics"), 12, 200),
            openQuestions=self._string_list(payload.get("openQuestions"), 12, 300),
            sourceEventIds=source_event_ids,
            sourceMessageCount=len(messages),
            generatedByModel=True,
            generatedAt=datetime.now(timezone.utc).isoformat(),
        )

    @staticmethod
    def _field(value: object, max_length: int) -> CognitionFieldResult:
        """把模型字段规范成文本与 0 到 1 的置信度，异常结构自动降级为空字段。"""
        if not isinstance(value, dict):
            return CognitionFieldResult()
        text = " ".join(str(value.get("value") or "").split()).strip()[:max_length]
        try:
            confidence = float(value.get("confidence") or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        return CognitionFieldResult(value=text, confidence=max(0.0, min(1.0, confidence)))

    @staticmethod
    def _string_list(value: object, max_items: int, max_length: int) -> list[str]:
        """清理模型列表并保持顺序去重，避免认知卡无限增长。"""
        if not isinstance(value, list):
            return []
        result: list[str] = []
        for item in value:
            text = " ".join(str(item or "").split()).strip()[:max_length]
            if text and text not in result:
                result.append(text)
            if len(result) >= max_items:
                break
        return result

    def _fallback_response(
        self,
        messages: list[ConversationProgressMessage],
        source_event_ids: list[str],
    ) -> ConversationCognitionResponse:
        """模型不可用时仅陈述最后一条消息对应的客观轮次状态，不推断关系和人格。"""
        progress = CognitionFieldResult()
        if messages:
            latest = messages[-1]
            if self._is_own_message(latest):
                value = "账号主人一侧已发送最新消息，当前等待对方继续回复"
            else:
                value = "对方已发送最新消息，当前轮到账号主人一侧处理"
            progress = CognitionFieldResult(value=value, confidence=1.0)
        return ConversationCognitionResponse(
            currentProgress=progress,
            sourceEventIds=source_event_ids,
            sourceMessageCount=len(messages),
            generatedByModel=False,
            generatedAt=datetime.now(timezone.utc).isoformat(),
        )
