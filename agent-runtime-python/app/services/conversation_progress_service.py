from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from app.clients.event_center_service import EventCenterServiceClient
from app.clients.llm_service import LlmServiceClient
from app.schemas.conversation_progress import (
    ConversationProgressMessage,
    ConversationProgressRequest,
    ConversationProgressResponse,
)


logger = logging.getLogger(__name__)


class ConversationProgressService:
    """只读分析会话时间线，在用户打开上下文时生成一次自然语言进度。"""

    def __init__(
        self,
        event_center_client: EventCenterServiceClient,
        llm_client: LlmServiceClient,
    ) -> None:
        # 这个构造函数的作用是注入模型配置解析客户端和统一 LLM 客户端。
        self.event_center_client = event_center_client
        self.llm_client = llm_client

    async def summarize(self, request: ConversationProgressRequest) -> ConversationProgressResponse:
        """根据完整双方时间线生成进度摘要；模型不可用时返回稳定的本地自然语言概括。"""
        messages = self._chronological_messages(request.messages)
        fallback_summary = self._build_fallback_summary(messages)
        generated_at = datetime.now(timezone.utc).isoformat()
        if not messages:
            return ConversationProgressResponse(
                summary=fallback_summary,
                generatedByModel=False,
                generatedAt=generated_at,
            )

        try:
            resolved = await self.event_center_client.resolve_user_model_profile(
                route="chat_summary",
                user_id=request.user_id,
            )
            model_profile = resolved.profile if resolved and resolved.matched else None
            if not self.llm_client.is_enabled(model_profile):
                raise RuntimeError("chat_summary model is not configured")

            summary = await self.llm_client.generate_reply(
                system_prompt=self._build_system_prompt(),
                user_message=self._build_transcript(request, messages),
                temperature=0.2,
                model_profile=model_profile,
            )
            normalized_summary = self._normalize_model_summary(summary)
            if not normalized_summary:
                raise RuntimeError("conversation progress summary is empty")
            return ConversationProgressResponse(
                summary=normalized_summary,
                generatedByModel=True,
                generatedAt=generated_at,
            )
        except Exception as exception:
            # 进度摘要属于辅助展示，模型超时或配置错误不能阻止用户查看真实聊天记录。
            logger.warning(
                "会话进度模型摘要失败，已使用本地概括：platform=%s, chatType=%s, chatId=%s, error=%s",
                request.platform,
                request.chat_type,
                request.chat_id,
                exception,
            )
            return ConversationProgressResponse(
                summary=fallback_summary,
                generatedByModel=False,
                generatedAt=generated_at,
            )

    @staticmethod
    def _build_system_prompt() -> str:
        """构造专用于工作台进度卡片的提示词，禁止模型把摘要写成回复或行动建议。"""
        return (
            "你是 Memo Echo 的会话进度分析员，只负责分析给定聊天记录，不参与聊天。"
            "请严格区分‘对方’、‘我’和‘Agent代发’，不要颠倒双方身份。"
            "输出一个 45 到 110 字的中文自然段，清楚说明：对方当前在谈什么或想解决什么，"
            "我方或 Agent 已经回应到哪一步，以及此刻轮到哪一方继续。"
            "如果记录明确显示自动回复暂停或需要人工确认，要直接说明。"
            "只能依据给定记录，不猜测动机，不补充事实，不逐条复述，不给下一步建议。"
            "不要使用标题、列表、Markdown、引号外的说明或‘作为AI’等自我介绍。"
        )

    def _build_transcript(
        self,
        request: ConversationProgressRequest,
        messages: list[ConversationProgressMessage],
    ) -> str:
        """把结构化消息转换成带明确身份标签的短时间线，避免模型混淆聊天双方。"""
        lines = [
            f"平台：{request.platform}",
            f"会话类型：{request.chat_type}",
            "以下消息按时间从旧到新排列：",
        ]
        for message in messages[-40:]:
            label = self._speaker_label(message)
            text = self._message_text(message)
            status = " [等待人工确认]" if message.need_human_confirmation else ""
            lines.append(f"[{label}]{status} {text}")
        return "\n".join(lines)

    @staticmethod
    def _chronological_messages(
        messages: list[ConversationProgressMessage],
    ) -> list[ConversationProgressMessage]:
        """按时间从旧到新排列消息，并去掉完全没有文本和附件的空记录。"""
        readable = [message for message in messages if message.text.strip() or message.attachments]
        return sorted(readable, key=lambda message: message.timestamp or "")

    @staticmethod
    def _is_own_message(message: ConversationProgressMessage) -> bool:
        """依据 Event Center 的可信来源字段判断是否为本人或 Agent 已实际发送的消息。"""
        origin = message.message_origin.strip().upper()
        return origin in {"USER_MANUAL", "AGENT_AUTO", "AGENT_CONFIRMED"} or message.sender_role == "self"

    def _speaker_label(self, message: ConversationProgressMessage) -> str:
        """为模型生成稳定身份标签，让 Agent 代发与本人手动发送既同属我方又可被区分。"""
        origin = message.message_origin.strip().upper()
        if origin in {"AGENT_AUTO", "AGENT_CONFIRMED"}:
            return "Agent代发"
        if self._is_own_message(message):
            return "我"
        return "对方"

    @staticmethod
    def _message_text(message: ConversationProgressMessage) -> str:
        """提取可进入摘要的消息文本；纯附件消息使用中性占位，不臆测附件内容。"""
        text = " ".join(message.text.split()).strip()
        if text:
            return text[:180]
        if message.attachments:
            return f"发送了 {len(message.attachments)} 个附件"
        return "发送了一条无文本消息"

    def _build_fallback_summary(self, messages: list[ConversationProgressMessage]) -> str:
        """在模型不可用时用双方最后发言生成可读进度，避免重新退化成系统状态拼接。"""
        if not messages:
            return "当前还没有可用于判断聊天进度的消息记录"

        latest = messages[-1]
        latest_peer = next((item for item in reversed(messages) if not self._is_own_message(item)), None)
        latest_own = next((item for item in reversed(messages) if self._is_own_message(item)), None)
        waiting = next((item for item in reversed(messages) if item.need_human_confirmation), None)
        peer_text = self._short_quote(latest_peer) if latest_peer else "暂无明确的新内容"
        own_text = self._short_quote(latest_own) if latest_own else "尚未作出回应"

        if waiting is not None:
            return f"对方最近提到{peer_text}，Agent 已暂停自动回复，当前会话停在等待你确认处理的阶段"
        if self._is_own_message(latest):
            return f"对方最近提到{peer_text}，我方随后回应{own_text}，这一轮已经回复完成，目前在等对方继续"
        if latest_own is not None:
            return f"我方此前回应{own_text}，对方最新提到{peer_text}，当前消息还没有回复，进度停在我方处理阶段"
        return f"对方最近提到{peer_text}，当前还没有我方回复，进度停在等待我方回应的阶段"

    def _short_quote(self, message: ConversationProgressMessage | None) -> str:
        """把最后一条消息压缩成适合嵌入自然语言摘要的短引用。"""
        if message is None:
            return "“暂无内容”"
        text = self._message_text(message)
        return f"“{text[:46]}”"

    @staticmethod
    def _normalize_model_summary(summary: str) -> str:
        """清理模型可能附带的 Markdown 和多余换行，只保留单段工作台文案。"""
        text = " ".join(line.strip() for line in str(summary or "").splitlines() if line.strip())
        text = re.sub(r"^(?:#+|[-*]+)\s*", "", text).strip()
        text = text.strip("`\"'“”")
        return text[:220]
