from __future__ import annotations

import unittest

from app.agents.inbox_dispatch_agent import InboxDispatchAgent
from app.schemas.events import Sender, UnifiedEvent
from app.schemas.tasks import AgentTaskContext
from app.services.slow_channel_buffer import SlowChannelBuffer
from app.tools.registry import ToolRegistry


class InboxDispatchAgentTest(unittest.IsolatedAsyncioTestCase):
    async def test_should_mark_message_urgent_only_when_at_self(self) -> None:
        agent = InboxDispatchAgent(ToolRegistry(), SlowChannelBuffer(window_seconds=600, max_messages=10))
        event = UnifiedEvent(
            eventId="qq:message:group:10001",
            platform="qq",
            scene="life",
            eventType="message",
            chatType="group",
            chatId="138178088",
            sender=Sender(id="2597164807", name="freeze", role="owner"),
            text="[CQ:at,qq=3969785168] 最近有什么安排",
            attachments=[],
            mentions=["3969785168"],
            timestamp="2026-07-06T17:00:00+08:00",
            rawPayload={"self_id": 3969785168},
        )
        context = AgentTaskContext(task_id="task-001", route="chat_summary", event=event)

        result = await agent.run(context, "dispatch_message")

        self.assertEqual(result.structured_result["dispatchMode"], "urgent")
        self.assertEqual(result.structured_result["urgencyReason"], "at_self")
        self.assertTrue(result.structured_result["shouldNotifyNow"])

    async def test_should_mark_message_urgent_when_mentions_missing_but_raw_payload_has_at_segment(self) -> None:
        agent = InboxDispatchAgent(ToolRegistry(), SlowChannelBuffer(window_seconds=600, max_messages=10))
        event = UnifiedEvent(
            eventId="qq:message:group:10005",
            platform="qq",
            scene="life",
            eventType="message",
            chatType="group",
            chatId="138178088",
            sender=Sender(id="2597164807", name="freeze", role="owner"),
            text="[CQ:at,qq=3969785168] schedule for today",
            attachments=[],
            mentions=[],
            timestamp="2026-07-07T15:46:00+08:00",
            rawPayload={
                "self_id": 3969785168,
                "message": [
                    {"type": "at", "data": {"qq": "3969785168"}},
                    {"type": "text", "data": {"text": " schedule for today"}},
                ],
            },
        )
        context = AgentTaskContext(task_id="task-005", route="chat_summary", event=event)

        result = await agent.run(context, "dispatch_message")

        self.assertEqual(result.structured_result["dispatchMode"], "urgent")
        self.assertEqual(result.structured_result["urgencyReason"], "at_self")

    async def test_should_not_treat_at_all_as_at_self(self) -> None:
        agent = InboxDispatchAgent(ToolRegistry(), SlowChannelBuffer(window_seconds=600, max_messages=10))
        event = UnifiedEvent(
            eventId="qq:message:group:10002",
            platform="qq",
            scene="life",
            eventType="message",
            chatType="group",
            chatId="138178088",
            sender=Sender(id="2597164807", name="freeze", role="owner"),
            text="@所有人 晚上一起打球吗",
            attachments=[],
            mentions=["all"],
            timestamp="2026-07-06T17:01:00+08:00",
            rawPayload={"self_id": 3969785168},
        )
        context = AgentTaskContext(task_id="task-002", route="chat_summary", event=event)

        result = await agent.run(context, "dispatch_message")

        self.assertEqual(result.structured_result["dispatchMode"], "normal")
        self.assertEqual(result.structured_result["urgencyReason"], "none")

    async def test_should_buffer_normal_group_messages_and_flush_on_threshold(self) -> None:
        agent = InboxDispatchAgent(ToolRegistry(), SlowChannelBuffer(window_seconds=600, max_messages=2))
        first_event = UnifiedEvent(
            eventId="qq:message:group:10003",
            platform="qq",
            scene="life",
            eventType="message",
            chatType="group",
            chatId="138178088",
            sender=Sender(id="10001", name="alice", role=None),
            text="晚上一起吃饭吗",
            attachments=[],
            mentions=[],
            timestamp="2026-07-06T17:02:00+08:00",
            rawPayload={"self_id": 3969785168},
        )
        second_event = UnifiedEvent(
            eventId="qq:message:group:10004",
            platform="qq",
            scene="life",
            eventType="message",
            chatType="group",
            chatId="138178088",
            sender=Sender(id="10002", name="bob", role=None),
            text="我七点到",
            attachments=[],
            mentions=[],
            timestamp="2026-07-06T17:03:00+08:00",
            rawPayload={"self_id": 3969785168},
        )

        first_context = AgentTaskContext(task_id="task-003", route="chat_summary", event=first_event)
        second_context = AgentTaskContext(task_id="task-004", route="chat_summary", event=second_event)

        first_result = await agent.run(first_context, "dispatch_message")
        second_result = await agent.run(second_context, "dispatch_message")

        self.assertEqual(first_result.structured_result["dispatchMode"], "normal")
        self.assertFalse(first_result.structured_result["shouldNotifyNow"])
        self.assertFalse(first_result.structured_result["flushed"])

        self.assertEqual(second_result.structured_result["dispatchMode"], "normal")
        self.assertTrue(second_result.structured_result["shouldNotifyNow"])
        self.assertTrue(second_result.structured_result["flushed"])
        self.assertIn("过去一段时间群里主要提到：", second_result.reply_draft)
        self.assertIn("alice", second_result.reply_draft)
        self.assertIn("bob", second_result.reply_draft)


if __name__ == "__main__":
    unittest.main()
