from __future__ import annotations

import unittest

from app.schemas.events import Sender, UnifiedEvent
from app.agents.social_agent import SocialAgent
from app.services.conversation_state_service import ConversationStateService


class ConversationStateServiceTest(unittest.TestCase):
    """验证开放状态始终来自事件时间线，而不是业务关键词猜测。"""

    def setUp(self) -> None:
        """为每个测试创建无外部依赖的确定性状态服务。"""
        self.service = ConversationStateService()

    def test_should_collect_consecutive_peer_messages_after_last_reply(self) -> None:
        """我方最后一次回复后的连续对方消息和当前消息应合并为同一待回应轮次。"""
        history = [
            self._history("peer-old", "CONTACT", "之前的问题"),
            self._history("agent-reply", "AGENT", "你继续说"),
            self._history("peer-one", "CONTACT", "第一条补充"),
            self._history("peer-two", "CONTACT", "第二条补充"),
        ]

        state = self.service.build(self._event("current", "第三条补充"), history)

        self.assertEqual("WAITING_AGENT", state.status)
        self.assertEqual("AGENT", state.responsible_party)
        self.assertEqual(
            ["peer-one", "peer-two", "current"],
            state.source_event_ids,
        )
        self.assertEqual("第三条补充", state.pending_items[-1].text)
        self.assertIn("3 条", state.summary)

    def test_should_prioritize_unresolved_human_confirmation(self) -> None:
        """待人工确认状态应优先于普通待回复消息，避免暂停后继续自动发送。"""
        history = [
            self._history("owner-old", "OWNER", "我先看看"),
            self._history(
                "blocked-candidate",
                "CONTACT",
                "需要你确认的信息",
                need_human_confirmation=True,
            ),
        ]

        state = self.service.build(self._event("current", "还在吗"), history)

        self.assertEqual("WAITING_OWNER_CONFIRMATION", state.status)
        self.assertEqual("OWNER", state.responsible_party)
        self.assertEqual(["blocked-candidate"], state.source_event_ids)

    def test_owner_message_should_resolve_older_confirmation(self) -> None:
        """账号主人在待确认事件之后亲自发言时，旧确认状态不应永久阻塞会话。"""
        history = [
            self._history(
                "blocked-candidate",
                "CONTACT",
                "需要你确认的信息",
                need_human_confirmation=True,
            ),
            self._history("owner-resolution", "OWNER", "我已经处理了"),
        ]

        state = self.service.build(self._event("current", "知道了"), history)

        self.assertEqual("WAITING_AGENT", state.status)
        self.assertEqual(["current"], state.source_event_ids)

    def test_should_wait_for_peer_after_outgoing_message(self) -> None:
        """当前事件属于自身回显时不应生成待回复项，并应保持等待对方状态。"""
        history = [self._history("agent-last", "AGENT", "晚点联系")]
        event = self._event("owner-echo", "好", actor_type="OWNER", sender_id="bot-001")

        state = self.service.build(event, history)

        self.assertEqual("WAITING_PEER", state.status)
        self.assertEqual("PEER", state.responsible_party)
        self.assertEqual([], state.pending_items)

    def test_should_inject_traceable_open_state_into_social_prompt(self) -> None:
        """SocialAgent 提示词应携带待回应原文和事件 ID，但不能扩写业务含义。"""
        state = self.service.build(self._event("current-source", "把时间改到三点"), [])

        prompt = SocialAgent._append_conversation_state("基础提示词", state)

        self.assertIn("[当前会话开放状态]", prompt)
        self.assertIn("current-source", prompt)
        self.assertIn("把时间改到三点", prompt)
        self.assertIn("不得根据状态名称虚构付款、交付", prompt)

    @staticmethod
    def _history(
        event_id: str,
        actor_type: str,
        text: str,
        need_human_confirmation: bool = False,
    ) -> dict:
        """构造一条已按时间升序排列的历史消息。"""
        return {
            "eventId": event_id,
            "actorType": actor_type,
            "role": "self" if actor_type in {"OWNER", "AGENT"} else "peer",
            "text": text,
            "timestamp": f"2026-07-17T10:0{len(event_id) % 10}:00+08:00",
            "needHumanConfirmation": need_human_confirmation,
        }

    @staticmethod
    def _event(
        event_id: str,
        text: str,
        actor_type: str = "CONTACT",
        sender_id: str = "friend-001",
    ) -> UnifiedEvent:
        """构造当前统一事件，并显式指定参与者身份。"""
        return UnifiedEvent(
            eventId=event_id,
            platform="qq",
            scene="life",
            eventType="message",
            chatType="private",
            chatId="friend-001",
            selfId="bot-001",
            sender=Sender(id=sender_id, name="联系人", role=None),
            text=text,
            attachments=[],
            mentions=[],
            timestamp="2026-07-17T10:10:00+08:00",
            rawPayload={},
            actorType=actor_type,
        )
