from __future__ import annotations

from app.schemas.conversation_state import ConversationOpenState, OpenConversationItem
from app.schemas.events import UnifiedEvent


class ConversationStateService:
    """根据可信时间线确定当前会话轮到谁处理，不推测具体业务状态。"""

    _MAX_PENDING_ITEMS = 6
    _MAX_ITEM_CHARS = 160

    def build(
        self,
        event: UnifiedEvent,
        history_context: list[dict],
    ) -> ConversationOpenState:
        """合并历史与当前事件，生成可供 Agent 使用的开放状态快照。"""
        last_owner_index = self._find_last_actor_index(history_context, {"OWNER"})
        confirmation_items = self._find_unresolved_confirmations(history_context, last_owner_index)
        if confirmation_items:
            return self._build_state(
                status="WAITING_OWNER_CONFIRMATION",
                responsible_party="OWNER",
                summary="存在尚未完成人工确认的候选回复，自动代理应保持暂停",
                pending_items=confirmation_items,
            )

        last_outgoing_index = self._find_last_actor_index(history_context, {"OWNER", "AGENT"})
        pending_peer_items = self._find_peer_messages_after(history_context, last_outgoing_index)
        current_item = self._build_current_event_item(event)
        if current_item is not None:
            pending_peer_items.append(current_item)
        pending_peer_items = self._deduplicate_items(pending_peer_items)[-self._MAX_PENDING_ITEMS :]
        if pending_peer_items:
            return self._build_state(
                status="WAITING_AGENT",
                responsible_party="AGENT",
                summary=self._summarize_pending_peer_messages(pending_peer_items),
                pending_items=pending_peer_items,
            )

        latest_actor = self._latest_actor(history_context)
        if latest_actor in {"OWNER", "AGENT"}:
            return ConversationOpenState(
                status="WAITING_PEER",
                responsibleParty="PEER",
                summary="我方已经发送回复，当前等待对方继续",
            )
        return ConversationOpenState()

    def _find_unresolved_confirmations(
        self,
        history_context: list[dict],
        last_owner_index: int,
    ) -> list[OpenConversationItem]:
        """查找账号主人最后一次人工发言后仍处于待确认状态的事件。"""
        items: list[OpenConversationItem] = []
        for index, message in enumerate(history_context):
            if index <= last_owner_index or not bool(message.get("needHumanConfirmation")):
                continue
            items.append(self._build_history_item(message, reason="awaiting_owner_confirmation"))
        return self._deduplicate_items(items)[-self._MAX_PENDING_ITEMS :]

    def _find_peer_messages_after(
        self,
        history_context: list[dict],
        last_outgoing_index: int,
    ) -> list[OpenConversationItem]:
        """提取我方最后一次回复之后，对方连续发送但尚未回应的消息。"""
        items: list[OpenConversationItem] = []
        for index, message in enumerate(history_context):
            if index <= last_outgoing_index or self._resolve_actor(message) != "CONTACT":
                continue
            items.append(self._build_history_item(message, reason="awaiting_reply"))
        return items

    def _build_current_event_item(self, event: UnifiedEvent) -> OpenConversationItem | None:
        """把当前外部事件加入待回应集合；自身消息和代理回显不会形成新任务。"""
        actor_type = self._resolve_event_actor(event)
        if actor_type != "CONTACT":
            return None
        return OpenConversationItem(
            sourceEventId=event.event_id,
            actorType=actor_type,
            text=self._normalize_text(event.text),
            timestamp=event.timestamp,
            reason="awaiting_reply",
        )

    def _build_history_item(self, message: dict, reason: str) -> OpenConversationItem:
        """把历史消息转换为状态项，同时保留可追溯的事件 ID。"""
        return OpenConversationItem(
            sourceEventId=str(message.get("eventId") or ""),
            actorType=self._resolve_actor(message),
            text=self._normalize_text(message.get("text")),
            timestamp=str(message.get("timestamp") or ""),
            reason=reason,
        )

    @classmethod
    def _normalize_text(cls, text: object) -> str:
        """压缩空白并限制展示长度，非文本消息使用稳定占位符表示。"""
        normalized = " ".join(str(text or "").split())
        if not normalized:
            return "[非文本消息]"
        return normalized[: cls._MAX_ITEM_CHARS]

    @staticmethod
    def _resolve_actor(message: dict) -> str:
        """优先读取统一身份；旧记录仅根据 role 和来源字段兼容推导。"""
        actor_type = str(message.get("actorType") or "").upper()
        if actor_type in {"OWNER", "AGENT", "CONTACT", "SYSTEM"}:
            return actor_type
        origin = str(message.get("messageOrigin") or "").upper()
        if origin in {"AGENT_AUTO", "AGENT_CONFIRMED"}:
            return "AGENT"
        return "OWNER" if str(message.get("role") or "").lower() == "self" else "CONTACT"

    @staticmethod
    def _resolve_event_actor(event: UnifiedEvent) -> str:
        """识别当前事件参与者；新事件缺少 actorType 时使用双方 ID 兜底。"""
        actor_type = str(event.actor_type or "").upper()
        if actor_type in {"OWNER", "AGENT", "CONTACT", "SYSTEM"}:
            return actor_type
        if event.self_id and str(event.sender.id) == str(event.self_id):
            return "OWNER"
        return "CONTACT"

    @classmethod
    def _find_last_actor_index(cls, history_context: list[dict], actors: set[str]) -> int:
        """从后向前查找指定参与者最后一次发言所在位置。"""
        for index in range(len(history_context) - 1, -1, -1):
            if cls._resolve_actor(history_context[index]) in actors:
                return index
        return -1

    @classmethod
    def _latest_actor(cls, history_context: list[dict]) -> str:
        """返回时间线最后一条有效消息的参与者。"""
        if not history_context:
            return "SYSTEM"
        return cls._resolve_actor(history_context[-1])

    @staticmethod
    def _deduplicate_items(items: list[OpenConversationItem]) -> list[OpenConversationItem]:
        """按事件 ID 去重，缺少事件 ID 时使用参与者、时间和文本组成稳定键。"""
        result: list[OpenConversationItem] = []
        seen: set[str] = set()
        for item in items:
            key = item.source_event_id or f"{item.actor_type}|{item.timestamp}|{item.text}"
            if key in seen:
                continue
            seen.add(key)
            result.append(item)
        return result

    @staticmethod
    def _summarize_pending_peer_messages(items: list[OpenConversationItem]) -> str:
        """只描述待回应消息数量和原文，不对付款、交付等业务含义做关键词猜测。"""
        latest = items[-1].text
        if len(items) == 1:
            return f"对方有一条尚未回应的消息：{latest}"
        return f"对方连续发送了 {len(items)} 条尚未回应的消息，最新一条是：{latest}"

    @staticmethod
    def _build_state(
        status: str,
        responsible_party: str,
        summary: str,
        pending_items: list[OpenConversationItem],
    ) -> ConversationOpenState:
        """统一组装状态并同步生成来源事件 ID 列表。"""
        return ConversationOpenState(
            status=status,
            responsibleParty=responsible_party,
            summary=summary,
            sourceEventIds=[item.source_event_id for item in pending_items if item.source_event_id],
            pendingItems=pending_items,
        )
