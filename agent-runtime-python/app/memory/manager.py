from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.clients.event_center_service import EventCenterServiceClient
from app.schemas.events import UnifiedEvent
from app.schemas.memories import VerifiedMemory
from app.schemas.profiles import ConversationProfileMatchResult
from app.memory.context_compressor import HistoryContextCompressor
from app.memory.knowledge_retriever import KnowledgeRetriever


logger = logging.getLogger(__name__)


class MemoryManager:
    """负责读取短期上下文、外部知识和用户确认的长期记忆，不自行认定模型输出为事实。"""

    # 超过这个空闲时间就视为一次新的即时聊天。旧记录仍保存在 Event Center，
    # 但不会在普通新消息中冒充“刚刚发生”的活跃上下文。
    _ACTIVE_SESSION_GAP_SECONDS = 30 * 60
    # 普通私聊即使关闭“跨会话历史”，也必须保留当前连续会话，否则 Agent 每收到一条消息都会失忆。
    _ACTIVE_SESSION_MAX_MESSAGES = 16
    # 多轮 Skill 往往需要逐步收集资料，默认 12 条很容易被拆分回复和平台回显挤满。
    _SKILL_CONTEXT_MIN_MESSAGES = 32
    # NapCat 可能同时上报发送回显和 Event Center 的合成回复；短时间同文消息只保留一份。
    _SELF_ECHO_DEDUP_SECONDS = 10
    _ARCHIVED_CONTEXT_CUES = (
        "之前",
        "刚才",
        "上次",
        "前面",
        "还记得",
        "记不记得",
        "聊过",
        "说过",
        "提过",
        "你不是",
        "你刚",
    )

    def __init__(self, event_center_client: EventCenterServiceClient | None = None) -> None:
        # 上下文仍以 Event Center 为唯一数据源，Runtime 不额外复制聊天记录。
        self.event_center_client = event_center_client
        self.knowledge_retriever = KnowledgeRetriever()
        self.context_compressor = HistoryContextCompressor()

    async def build_verified_memories(self, event: UnifiedEvent) -> list[VerifiedMemory]:
        """读取当前作用域内的已确认长期记忆；服务不可用时降级为空列表而不阻塞回复。"""
        if self.event_center_client is None:
            return []
        try:
            return await self.event_center_client.list_verified_memories(event)
        except Exception as exception:
            logger.warning("读取已确认长期记忆失败，已跳过记忆注入：%s", exception)
            return []

    async def build_history_context(
        self,
        event: UnifiedEvent,
        profile_match: ConversationProfileMatchResult | None,
        skill_context_enabled: bool = False,
    ) -> list[dict[str, Any]]:
        """为私聊构造当前会话上下文，并按授权决定是否扩展到跨会话历史。"""
        profile = profile_match.profile if profile_match and profile_match.matched else None
        if (
            self.event_center_client is None
            or profile is None
            or event.chat_type.lower() != "private"
        ):
            return []

        # `privateHistoryEnabled` 只控制跨会话历史。当前 30 分钟内的连续对话属于完成本轮回复
        # 所必需的短期工作记忆，不能因为用户未授权导入旧聊天而被一并清空。
        archived_history_enabled = profile.private_history_enabled
        configured_limit = max(profile.history_max_messages, 1)
        if archived_history_enabled:
            message_limit = configured_limit
        else:
            message_limit = min(configured_limit, self._ACTIVE_SESSION_MAX_MESSAGES)
            message_limit = max(message_limit, self._ACTIVE_SESSION_MAX_MESSAGES)
        if skill_context_enabled:
            message_limit = max(message_limit, self._SKILL_CONTEXT_MIN_MESSAGES)
        message_limit = min(message_limit, 50)
        char_limit = min(max(profile.history_max_chars, 200), 12_000)
        # 多取一部分原始记录，给当前事件过滤和自身消息回显去重留出余量。
        fetch_limit = min(max(message_limit * 2 + 1, message_limit + 5), 100)
        try:
            messages = await self.event_center_client.list_conversation_messages(
                chat_id=event.chat_id,
                platform=event.platform,
                chat_type=event.chat_type,
                limit=fetch_limit,
                user_id=self.event_center_client.resolve_event_user_id(event),
            )
        except Exception as exception:
            # 历史读取失败不能阻塞正常回复，直接降级为无上下文。
            logger.warning("读取私聊历史失败，已跳过上下文注入：%s", exception)
            return []

        # 即使 Event Center 或历史导入返回乱序数据，也先在 Runtime 重新建立确定性的倒序时间线。
        # 这里从最新消息开始占用字符预算，避免旧长文本把真正相关的最近消息挤出上下文。
        messages = sorted(messages, key=self._message_sort_key, reverse=True)
        newest_first: list[dict[str, Any]] = []
        # 先扫描比最终窗口更大的原始范围，较早内容才能在超限时进入派生摘要。
        scan_char_limit = min(max(char_limit * 3, char_limit + 2_000), 30_000)
        scanned_chars = 0
        recent_self_messages: dict[str, datetime] = {}
        seen_message_identities: set[str] = set()
        seen_event_ids: set[str] = set()
        for message in messages:
            event_type = str(message.get("eventType") or "").strip().lower()
            message_origin = str(message.get("messageOrigin", "EXTERNAL")).upper()
            # 工作台委托命令属于控制面，不是 QQ 双方实际说过的话；旧数据也在这里统一排除。
            if event_type == "delegated_task_started" or message_origin == "INTERNAL":
                continue
            event_id = str(message.get("eventId", ""))
            if event_id == event.event_id:
                continue
            if event_id and event_id in seen_event_ids:
                continue
            if event_id:
                seen_event_ids.add(event_id)
            identity_key = self._message_identity_key(message)
            if identity_key and identity_key in seen_message_identities:
                continue
            if identity_key:
                seen_message_identities.add(identity_key)
            text = " ".join(str(message.get("text", "")).split())
            media_analysis = message.get("mediaAnalysis") or []
            if not text and not media_analysis:
                continue
            if not text:
                # 图片、文件和表情同样属于会话时间线，不能因为没有纯文本就从上下文中消失。
                text = "[非文本消息]"
            sender_id = str(message.get("senderId", ""))
            self_id = event.self_id or ""
            actor_type = str(message.get("actorType", "")).upper()
            role = "self" if self._is_self_message(
                sender_id,
                self_id,
                message_origin,
                actor_type,
            ) else "peer"
            # 只有没有统一消息 ID 的旧记录才使用同文本时间窗兜底，避免误删用户主动重复发送的消息。
            if not identity_key and role == "self" and self._is_duplicate_self_echo(
                text,
                self._effective_message_time(message),
                recent_self_messages,
            ):
                continue
            remaining = scan_char_limit - scanned_chars
            if remaining <= 0:
                break
            text = text[:remaining]
            newest_first.append(
                {
                    "eventId": event_id,
                    # senderId 是首选依据；来源标记兼容旧记录缺 selfId 或账号 ID 格式不一致。
                    "role": role,
                    "senderName": str(message.get("senderName", "")),
                    "text": text,
                    "timestamp": str(message.get("timestamp", "")),
                    "sentAt": str(message.get("sentAt") or message.get("timestamp") or ""),
                    "receivedAt": str(message.get("receivedAt") or ""),
                    "importedAt": str(message.get("importedAt") or ""),
                    "direction": str(message.get("direction") or self._legacy_direction(role)),
                    "delegatedTaskId": str(message.get("delegatedTaskId") or ""),
                    "messageOrigin": message_origin,
                    "actorType": actor_type or self._legacy_actor_type(role, message_origin),
                    "platformMessageId": str(message.get("platformMessageId", "")),
                    "clientMessageId": str(message.get("clientMessageId", "")),
                    "correlationId": str(message.get("correlationId", "")),
                    "sequence": message.get("sequence"),
                    # Agent 发过的话可以帮助衔接对话，但不能反过来证明账号主人真的做过某件事。
                    "factAuthority": self._resolve_fact_authority(role, message_origin, actor_type),
                    "mediaAnalysis": media_analysis,
                    # 开放状态只消费 Event Center 已记录的处理结果，不从消息文本猜测是否需要接管。
                    "processingStatus": str(message.get("processingStatus") or ""),
                    "needHumanConfirmation": bool(message.get("needHumanConfirmation", False)),
                    "writeBackStatus": str(message.get("writeBackStatus") or ""),
                }
            )
            scanned_chars += len(text)

        compacted = list(reversed(newest_first))
        if not archived_history_enabled:
            compacted = self._keep_relevant_session(compacted, event)
        elif not skill_context_enabled:
            # 普通人格会话继续隔离较早的临时状态；Skill 多轮流程则需要跨时段保留已确认资料。
            compacted = self._keep_relevant_session(compacted, event)
        compacted = self.context_compressor.compress(
            compacted,
            max_messages=message_limit,
            max_chars=char_limit,
        )
        derived_summary = next(
            (item for item in compacted if item.get("derivedSummary")),
            None,
        )
        logger.info(
            "私聊上下文加载完成：chatId=%s, messages=%d, self=%d, peer=%d, agentOutputs=%d, compressedSources=%d, archived=%s, skill=%s",
            event.chat_id,
            len(compacted),
            sum(item["role"] == "self" for item in compacted),
            sum(item["role"] == "peer" for item in compacted),
            sum(item["factAuthority"] == "agent_output" for item in compacted),
            int((derived_summary or {}).get("sourceCount") or 0),
            archived_history_enabled,
            skill_context_enabled,
        )
        return compacted

    @classmethod
    def _is_duplicate_self_echo(
        cls,
        text: str,
        timestamp: str,
        recent_self_messages: dict[str, datetime],
    ) -> bool:
        """去除 NapCat 自身消息回显与系统合成回复造成的短时间同文重复。"""
        normalized = "".join(str(text or "").split()).lower()
        if not normalized:
            return False
        message_time = cls._parse_timestamp(timestamp)
        previous_time = recent_self_messages.get(normalized)
        if message_time is not None:
            recent_self_messages[normalized] = message_time
        if previous_time is None or message_time is None:
            return False
        return abs((previous_time - message_time).total_seconds()) <= cls._SELF_ECHO_DEDUP_SECONDS

    @staticmethod
    def _is_self_message(
        sender_id: str,
        self_id: str,
        message_origin: str,
        actor_type: str = "",
    ) -> bool:
        """判断历史记录说话方；统一 actorType 优先，旧记录才回退到双方 ID 和来源标记。"""
        normalized_actor = str(actor_type or "").upper()
        if normalized_actor in {"OWNER", "AGENT"}:
            return True
        if normalized_actor in {"CONTACT", "SYSTEM"}:
            return False
        if self_id and sender_id:
            return sender_id == self_id
        return message_origin in {"USER_MANUAL", "AGENT_AUTO", "AGENT_CONFIRMED", "HISTORY_CONSENTED"}

    @staticmethod
    def _resolve_fact_authority(role: str, message_origin: str, actor_type: str = "") -> str:
        """区分真人发言与代理历史，防止模型把自己过去生成的草稿当作用户事实。"""
        if str(actor_type or "").upper() == "AGENT":
            return "agent_output"
        if role == "self" and message_origin in {"AGENT_AUTO", "AGENT_CONFIRMED"}:
            return "agent_output"
        if role == "self":
            return "human_self"
        return "peer_statement"

    @staticmethod
    def _legacy_actor_type(role: str, message_origin: str) -> str:
        """为旧历史补充仅供 Runtime 使用的参与者类型，不反写原始事件。"""
        if role == "peer":
            return "CONTACT"
        if message_origin in {"AGENT_AUTO", "AGENT_CONFIRMED"}:
            return "AGENT"
        return "OWNER"

    @classmethod
    def _message_sort_key(cls, message: dict[str, Any]) -> tuple[datetime, int, str]:
        """生成稳定排序键，保证延迟到达和同秒消息不会让上下文顺序随机变化。"""
        parsed = cls._parse_timestamp(cls._effective_message_time(message))
        timestamp = parsed or datetime.min.replace(tzinfo=timezone.utc)
        try:
            sequence = int(message.get("sequence"))
        except (TypeError, ValueError):
            sequence = -1
        return timestamp, sequence, str(message.get("eventId") or "")

    @staticmethod
    def _message_identity_key(message: dict[str, Any]) -> str:
        """生成跨 Webhook、合成回写和重复 HTTP 读取均稳定的消息幂等键。"""
        client_message_id = str(message.get("clientMessageId") or "").strip()
        if client_message_id:
            return f"client:{client_message_id}"
        platform_message_id = str(message.get("platformMessageId") or "").strip()
        if platform_message_id:
            return f"platform:{platform_message_id}"
        correlation_id = str(message.get("correlationId") or "").strip()
        if correlation_id:
            normalized_text = "".join(str(message.get("text") or "").split()).lower()
            sequence = str(message.get("sequence") or "")
            return f"correlation:{correlation_id}:{sequence}:{normalized_text}"
        # eventId 只能消除 API 自身重复返回，无法关联旧版平台回显，因此文本时间窗仍会处理这类旧记录。
        return ""

    @classmethod
    def _keep_relevant_session(
        cls,
        messages: list[dict[str, Any]],
        event: UnifiedEvent,
    ) -> list[dict[str, Any]]:
        """只向普通新消息注入当前连续会话；明确回顾旧聊天时才保留跨会话记录。"""
        if not messages or cls._requests_archived_context(event.text or ""):
            return messages

        current_time = cls._parse_timestamp(event.sent_at or event.timestamp or event.received_at or "")
        if current_time is None:
            return messages

        selected_newest_first: list[dict[str, Any]] = []
        newer_time = current_time
        for item in reversed(messages):
            message_time = cls._parse_timestamp(cls._effective_message_time(item))
            if message_time is not None:
                gap_seconds = (newer_time - message_time).total_seconds()
                if gap_seconds > cls._ACTIVE_SESSION_GAP_SECONDS:
                    break
                # 平台偶发乱序时不允许时间指针向未来移动。
                if message_time <= newer_time:
                    newer_time = message_time
            selected_newest_first.append(item)

        return list(reversed(selected_newest_first))

    @staticmethod
    def _effective_message_time(message: dict[str, Any]) -> str:
        """按平台发送、兼容时间、接收、导入的优先级返回可用于对话排序的时间。"""
        return str(
            message.get("sentAt")
            or message.get("timestamp")
            or message.get("receivedAt")
            or message.get("importedAt")
            or ""
        )

    @staticmethod
    def _legacy_direction(role: str) -> str:
        """为旧消息补充方向，使提示词可以明确区分对方发言和账号侧发言。"""
        return "OUTBOUND" if role == "self" else "INBOUND"

    @classmethod
    def _requests_archived_context(cls, text: str) -> bool:
        """识别用户是否明确在追问过去对话，避免会话切分破坏“还记得吗”类请求。"""
        normalized = "".join(str(text or "").split())
        return any(cue in normalized for cue in cls._ARCHIVED_CONTEXT_CUES)

    @staticmethod
    def _parse_timestamp(value: str) -> datetime | None:
        """兼容 ISO-8601 的 Z 和时区偏移，并统一转成 UTC 便于计算会话间隔。"""
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    async def build_retrieved_knowledge(
        self,
        event: UnifiedEvent,
        profile_match: ConversationProfileMatchResult | None,
    ) -> list[dict[str, Any]]:
        """检索当前设定集绑定的资料，失败时降级为空知识而不影响正常聊天。"""
        profile = profile_match.profile if profile_match and profile_match.matched else None
        if profile is None:
            return []
        try:
            return await self.knowledge_retriever.retrieve(event, profile)
        except Exception as exception:
            logger.warning("外部知识库检索失败，已跳过本次资料注入：%s", exception)
            return []
