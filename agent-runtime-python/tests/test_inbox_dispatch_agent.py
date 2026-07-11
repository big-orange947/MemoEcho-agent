from __future__ import annotations

import unittest
import asyncio

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

    async def test_digest_only_policy_should_buffer_at_message_instead_of_notifying_now(self) -> None:
        """验证摘要模式会覆盖 @ 自身的默认即时通道，避免特定群聊频繁打断用户。"""
        agent = InboxDispatchAgent(ToolRegistry(), SlowChannelBuffer(window_seconds=600, max_messages=10))
        event = UnifiedEvent(
            eventId="qq:message:group:10006",
            platform="qq",
            scene="life",
            eventType="message",
            chatType="group",
            chatId="138178088",
            selfId="3969785168",
            sender=Sender(id="2597164807", name="freeze", role="owner"),
            text="[CQ:at,qq=3969785168] 下午两点开会",
            attachments=[],
            mentions=["3969785168"],
            timestamp="2026-07-10T08:00:00+08:00",
            rawPayload={"self_id": 3969785168},
        )
        context = AgentTaskContext(
            task_id="task-006",
            route="chat_summary",
            event=event,
            metadata={
                "conversation_profile_match": {
                    "profile": {
                        "notificationMode": "DIGEST_ONLY",
                        "digestMaxMessages": 5,
                    }
                }
            },
        )

        result = await agent.run(context, "dispatch_message")

        self.assertEqual(result.structured_result["dispatchMode"], "normal")
        self.assertFalse(result.structured_result["shouldNotifyNow"])
        self.assertEqual(result.structured_result["notificationPolicy"], "DIGEST_ONLY")

    async def test_urgent_only_policy_should_suppress_normal_group_message(self) -> None:
        """验证仅重点模式会保留消息历史但不对普通群聊消息产生提醒或摘要。"""
        agent = InboxDispatchAgent(ToolRegistry(), SlowChannelBuffer(window_seconds=600, max_messages=10))
        event = UnifiedEvent(
            eventId="qq:message:group:10007",
            platform="qq",
            scene="life",
            eventType="message",
            chatType="group",
            chatId="138178088",
            sender=Sender(id="2597164807", name="freeze", role="owner"),
            text="晚上一起吃饭吗",
            attachments=[],
            mentions=[],
            timestamp="2026-07-10T08:01:00+08:00",
            rawPayload={"self_id": 3969785168},
        )
        context = AgentTaskContext(
            task_id="task-007",
            route="chat_summary",
            event=event,
            metadata={"conversation_profile_match": {"profile": {"notificationMode": "URGENT_ONLY"}}},
        )

        result = await agent.run(context, "dispatch_message")

        self.assertFalse(result.structured_result["shouldNotifyNow"])
        self.assertTrue(result.structured_result["suppressedByPolicy"])
        self.assertEqual(result.structured_result["flushReason"], "policy_suppressed")

    async def test_should_flush_buffer_when_time_window_elapses_without_new_message(self) -> None:
        """验证慢通道时间窗口到期后会主动调用回调，不依赖下一条消息才能产出摘要。"""
        flushed = []

        async def on_flush(result) -> None:
            flushed.append(result)

        buffer = SlowChannelBuffer(window_seconds=1, max_messages=10, on_flush=on_flush)
        agent = InboxDispatchAgent(ToolRegistry(), buffer)
        event = UnifiedEvent(
            eventId="qq:message:group:10008",
            platform="qq",
            scene="life",
            eventType="message",
            chatType="group",
            chatId="138178088",
            sender=Sender(id="2597164807", name="freeze", role="owner"),
            text="窗口到期测试消息",
            attachments=[],
            mentions=[],
            timestamp="2026-07-10T08:02:00+08:00",
            rawPayload={"self_id": 3969785168},
        )

        await agent.run(AgentTaskContext(task_id="task-008", route="chat_summary", event=event), "dispatch_message")
        await asyncio.sleep(1.05)

        self.assertEqual(len(flushed), 1)
        self.assertEqual(flushed[0].message_count, 1)
        self.assertIn("窗口到期测试消息", flushed[0].summary)

    async def test_should_include_urgent_message_in_digest_without_triggering_threshold(self) -> None:
        """验证快通道消息可作为摘要上下文保存，但不会单独触发数量阈值产生重复提醒。"""
        agent = InboxDispatchAgent(ToolRegistry(), SlowChannelBuffer(window_seconds=600, max_messages=1))
        event = UnifiedEvent(
            eventId="qq:message:group:10009",
            platform="qq",
            scene="life",
            eventType="message",
            chatType="group",
            chatId="138178088",
            selfId="3969785168",
            sender=Sender(id="2597164807", name="freeze", role="owner"),
            text="[CQ:at,qq=3969785168] 请查看截止时间",
            attachments=[],
            mentions=["3969785168"],
            timestamp="2026-07-10T08:03:00+08:00",
            rawPayload={"self_id": 3969785168},
        )
        context = AgentTaskContext(
            task_id="task-009",
            route="chat_summary",
            event=event,
            metadata={
                "conversation_profile_match": {
                    "profile": {"notificationMode": "AUTO", "includeUrgentInDigest": True}
                }
            },
        )

        result = await agent.run(context, "dispatch_message")

        self.assertTrue(result.structured_result["shouldNotifyNow"])
        self.assertTrue(result.structured_result["includedInDigest"])
        self.assertEqual(result.structured_result["bufferedCount"], 1)


if __name__ == "__main__":
    unittest.main()
