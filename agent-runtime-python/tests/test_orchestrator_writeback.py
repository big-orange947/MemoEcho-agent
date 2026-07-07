from __future__ import annotations

import unittest

from app.memory.manager import MemoryManager
from app.orchestrator.service import OrchestratorService
from app.planner.service import PlannerService
from app.router.service import RouterService
from app.schemas.events import Sender, UnifiedEvent
from app.services.slow_channel_buffer import SlowChannelBuffer
from app.tools.registry import ToolRegistry


class DummySendQqMessageTool:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def execute(self, **kwargs):
        self.calls.append(kwargs)
        return {"status": "ok"}


class OrchestratorWriteBackTest(unittest.IsolatedAsyncioTestCase):
    async def test_should_send_group_summary_when_slow_channel_flushes(self) -> None:
        tools = ToolRegistry()
        send_tool = DummySendQqMessageTool()
        tools.register("send_qq_message", send_tool)
        service = OrchestratorService(
            router=RouterService(),
            planner=PlannerService(),
            tools=tools,
            memory=MemoryManager(),
            slow_channel_buffer=SlowChannelBuffer(window_seconds=600, max_messages=1),
        )

        event = UnifiedEvent(
            eventId="qq:message:group:30001",
            platform="qq",
            scene="life",
            eventType="message",
            chatType="group",
            chatId="138178088",
            sender=Sender(id="10001", name="alice", role=None),
            text="晚上一起吃饭吗",
            attachments=[],
            mentions=[],
            timestamp="2026-07-07T00:20:00+08:00",
            rawPayload={"self_id": 3969785168},
        )

        result = await service.handle_event(event)

        self.assertEqual(result.route, "message_dispatch")
        self.assertEqual(len(send_tool.calls), 1)
        self.assertEqual(send_tool.calls[0]["chat_type"], "group")
        self.assertEqual(send_tool.calls[0]["chat_id"], "138178088")
        self.assertIn("过去一段时间群里主要提到：", send_tool.calls[0]["message"])
        self.assertIn("qq_write_back_sent:ok", result.write_back_actions)

    async def test_should_send_at_reply_back_to_group_when_message_mentions_self(self) -> None:
        tools = ToolRegistry()
        send_tool = DummySendQqMessageTool()
        tools.register("send_qq_message", send_tool)
        service = OrchestratorService(
            router=RouterService(),
            planner=PlannerService(),
            tools=tools,
            memory=MemoryManager(),
            slow_channel_buffer=SlowChannelBuffer(window_seconds=600, max_messages=10),
        )

        event = UnifiedEvent(
            eventId="qq:message:group:30002",
            platform="qq",
            scene="life",
            eventType="message",
            chatType="group",
            chatId="138178088",
            selfId="3969785168",
            sender=Sender(id="10001", name="alice", role=None),
            text="[CQ:at,qq=3969785168] schedule today",
            attachments=[],
            mentions=["3969785168"],
            timestamp="2026-07-07T00:21:00+08:00",
            rawPayload={"self_id": 3969785168},
        )

        result = await service.handle_event(event)

        self.assertEqual(result.route, "schedule_extract")
        self.assertEqual(len(send_tool.calls), 1)
        self.assertEqual(send_tool.calls[0]["chat_type"], "group")
        self.assertEqual(send_tool.calls[0]["chat_id"], "138178088")
        self.assertIn("segments", send_tool.calls[0])
        self.assertEqual(send_tool.calls[0]["segments"][0]["type"], "at")
        self.assertEqual(send_tool.calls[0]["segments"][0]["data"]["qq"], "10001")
        self.assertIn("qq_write_back_sent:ok", result.write_back_actions)


if __name__ == "__main__":
    unittest.main()
