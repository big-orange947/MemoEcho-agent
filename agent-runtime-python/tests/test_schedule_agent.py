from __future__ import annotations

import unittest

from app.agents.schedule_agent import ScheduleAgent
from app.schemas.events import Sender, UnifiedEvent
from app.schemas.tasks import AgentTaskContext
from app.tools.registry import ToolRegistry


class DummyCreateScheduleTool:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def execute(self, **kwargs):
        payload = kwargs["payload"]
        self.calls.append(payload)
        return {"id": "schedule-001", **payload}


class ScheduleAgentTest(unittest.IsolatedAsyncioTestCase):
    async def test_should_persist_schedule_when_start_time_exists(self) -> None:
        registry = ToolRegistry()
        tool = DummyCreateScheduleTool()
        registry.register("create_schedule", tool)
        agent = ScheduleAgent(registry)

        event = UnifiedEvent(
            eventId="qq:message:group:1843661133",
            platform="qq",
            scene="life",
            eventType="message",
            chatType="group",
            chatId="138178088",
            sender=Sender(id="2597164807", name="freeze", role="owner"),
            text="【考研经验分享会】2026年7月6日14:00在A01-N105举办分享会，欢迎感兴趣的同学参加。",
            attachments=[],
            mentions=[],
            timestamp="2026-07-06T10:00:00+08:00",
            rawPayload={},
        )

        context = AgentTaskContext(
            task_id="task-001",
            route="schedule_extract",
            event=event,
            allowed_tools=["create_schedule"],
        )

        result = await agent.run(context, "extract_schedule")

        self.assertEqual(result.agent, "schedule")
        self.assertEqual(result.structured_result["title"], "考研经验分享会")
        self.assertEqual(result.structured_result["start_time"], "2026-07-06 14:00:00")
        self.assertEqual(result.structured_result["location"], "A01-N105")
        self.assertIn("persisted_schedule", result.structured_result)
        self.assertEqual(len(tool.calls), 1)
        self.assertEqual(tool.calls[0]["sourceEventId"], "qq:message:group:1843661133")


if __name__ == "__main__":
    unittest.main()
