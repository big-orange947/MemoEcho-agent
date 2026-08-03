from __future__ import annotations

import unittest

from app.orchestrator.service import OrchestratorService
from app.schemas.events import Sender, UnifiedEvent


def build_peer_event(event_id: str, chat_id: str, text: str) -> UnifiedEvent:
    """构造联系人发来的私聊事件，验证会话级并发控制。"""
    return UnifiedEvent(
        eventId=event_id,
        platform="qq",
        scene="social",
        eventType="message",
        chatType="private",
        chatId=chat_id,
        sender=Sender(id=f"peer-{chat_id}", name=f"peer-{chat_id}", role=None),
        text=text,
        attachments=[],
        mentions=[],
        timestamp="2026-07-28T20:00:00+08:00",
        rawPayload={"messageOrigin": "EXTERNAL"},
    )


class DelegatedConversationVersionTest(unittest.IsolatedAsyncioTestCase):
    async def test_new_message_should_only_supersede_same_conversation(self) -> None:
        """同一会话的新消息淘汰旧推理，但不能影响另一个联系人的会话。"""
        service = OrchestratorService.__new__(OrchestratorService)
        service._delegated_conversation_versions = {}
        service._delegated_conversation_latest_event_ids = {}
        service._delegated_inbound_debounce_seconds = 0

        async def no_sleep(_: float) -> None:
            return None

        service.sleeper = no_sleep

        old_message = build_peer_event("qq:private:friend-a:old", "friend-a", "old message")
        other_conversation = build_peer_event("qq:private:friend-b:only", "friend-b", "other message")
        latest_message = build_peer_event("qq:private:friend-a:new", "friend-a", "latest message")

        old_version = service._register_delegated_peer_inbound(old_message)
        other_version = service._register_delegated_peer_inbound(other_conversation)
        latest_version = service._register_delegated_peer_inbound(latest_message)

        self.assertFalse(await service._wait_for_latest_delegated_inbound(old_message, old_version))
        self.assertTrue(await service._wait_for_latest_delegated_inbound(latest_message, latest_version))
        self.assertTrue(await service._wait_for_latest_delegated_inbound(other_conversation, other_version))
        self.assertTrue(service._is_delegated_write_back_superseded(old_message, {"id": "task-a"}))
        self.assertFalse(service._is_delegated_write_back_superseded(latest_message, {"id": "task-a"}))
        self.assertFalse(service._is_delegated_write_back_superseded(other_conversation, {"id": "task-b"}))

