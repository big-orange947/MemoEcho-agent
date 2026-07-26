from __future__ import annotations

import unittest

from app.agents.inbox_agent import InboxAgent
from app.schemas.events import Sender, UnifiedEvent
from app.schemas.tasks import AgentTaskContext
from app.services.slow_channel_buffer import SlowChannelFlush
from app.tools.registry import ToolRegistry
from tool_test_utils import register_test_tool


class DummyRecentMessagesTool:
    def __init__(self, messages: list[dict]) -> None:
        # 这个构造函数的作用是给测试注入固定消息列表，避免依赖真实 HTTP 服务。
        self.messages = messages
        self.calls: list[dict] = []

    async def execute(self, **kwargs):
        # 这个函数的作用是模拟最近消息查询工具，并记录调用参数供断言使用。
        self.calls.append(kwargs)
        return self.messages


class InboxAgentTest(unittest.IsolatedAsyncioTestCase):
    async def test_should_build_three_section_digest_for_slow_channel_batch(self) -> None:
        """验证慢通道批次通过 InboxAgent 产出发生、待办和下一步三段内容。"""
        event = UnifiedEvent(
            eventId="qq:message:group:batch-001",
            platform="qq",
            scene="life",
            eventType="message",
            chatType="group",
            chatId="1098307542",
            sender=Sender(id="10001", name="alice", role=None),
            text="deadline Friday",
            attachments=[],
            mentions=[],
            timestamp="2026-07-12T10:00:00+08:00",
            rawPayload={},
        )
        flush = SlowChannelFlush(
            aggregation_key="qq:group:1098307542",
            source_event=event,
            source_event_ids=[event.event_id],
            message_count=2,
            summary="fallback",
            transcript="alice: deadline Friday\nbob: document is ready",
        )

        result = await InboxAgent(ToolRegistry()).summarize_slow_channel_batch(flush)

        self.assertIn("deadline Friday", result["happened"])
        self.assertIn("deadline Friday", result["actionItems"])
        self.assertTrue(result["nextStep"])
        self.assertIn(result["happened"], result["summary"])

    async def test_should_build_summary_from_recent_messages(self) -> None:
        registry = ToolRegistry()
        tool = DummyRecentMessagesTool(
            [
                {
                    "eventId": "qq:message:group:3",
                    "chatId": "1098307542",
                    "chatName": "Memo Echo项目组",
                    "senderName": "freeze",
                    "text": "今天下午14:00在A01-N105开分享会",
                    "attachments": [],
                    "dispatchMode": "urgent",
                },
                {
                    "eventId": "qq:message:group:2",
                    "chatId": "1098307542",
                    "chatName": "Memo Echo项目组",
                    "senderName": "km",
                    "text": "记得带上项目演示文档",
                    "attachments": [{"fileName": "demo.pdf"}],
                    "dispatchMode": "normal",
                },
                {
                    "eventId": "qq:message:group:1",
                    "chatId": "1098307542",
                    "chatName": "Memo Echo项目组",
                    "senderName": "freeze",
                    "text": "今天下午14:00在A01-N105开分享会",
                    "attachments": [],
                    "dispatchMode": "urgent",
                },
            ]
        )
        register_test_tool(registry, "get_recent_messages", tool)
        agent = InboxAgent(registry)

        event = UnifiedEvent(
            eventId="qq:message:group:ask-001",
            platform="qq",
            scene="life",
            eventType="message",
            chatType="group",
            chatId="1098307542",
            selfId="3969785168",
            sender=Sender(id="2597164807", name="freeze", role="owner"),
            text="@哈吉仙 最近群里说了什么",
            attachments=[],
            mentions=["3969785168"],
            timestamp="2026-07-07T18:00:00+08:00",
            rawPayload={},
        )
        context = AgentTaskContext(task_id="task-001", route="chat_summary", event=event)

        result = await agent.run(context, "summarize_recent")

        self.assertEqual(result.agent, "inbox")
        self.assertEqual(result.structured_result["chat_name"], "Memo Echo项目组")
        self.assertEqual(result.structured_result["message_count"], 3)
        self.assertEqual(result.structured_result["attachment_count"], 1)
        self.assertEqual(result.structured_result["urgent_count"], 2)
        self.assertEqual(len(result.structured_result["highlights"]), 2)
        self.assertIn("最近的消息重点", result.reply_draft)
        self.assertIn("freeze：今天下午14:00在A01-N105开分享会", result.reply_draft)
        self.assertIn("km：记得带上项目演示文档", result.reply_draft)
        self.assertEqual(tool.calls[0]["chat_id"], "1098307542")

    async def test_should_fallback_when_recent_message_tool_is_missing(self) -> None:
        agent = InboxAgent(ToolRegistry())

        event = UnifiedEvent(
            eventId="qq:message:group:ask-002",
            platform="qq",
            scene="life",
            eventType="message",
            chatType="group",
            chatId="1098307542",
            selfId="3969785168",
            sender=Sender(id="2597164807", name="freeze", role="owner"),
            text="@哈吉仙 最近群里说了什么",
            attachments=[],
            mentions=["3969785168"],
            timestamp="2026-07-07T18:05:00+08:00",
            rawPayload={},
        )
        context = AgentTaskContext(task_id="task-002", route="chat_summary", event=event)

        result = await agent.run(context, "summarize_recent")

        self.assertEqual(result.agent, "inbox")
        self.assertEqual(result.structured_result["message_count"], 0)
        self.assertIn("暂时还没有整理到", result.reply_draft)
        self.assertIn("get_recent_messages tool is not registered", result.next_actions)


if __name__ == "__main__":
    unittest.main()
