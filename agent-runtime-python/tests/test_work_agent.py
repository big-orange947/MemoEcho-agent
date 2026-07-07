from __future__ import annotations

import unittest

from app.agents.work_agent import WorkAgent
from app.schemas.events import Sender, UnifiedEvent
from app.schemas.tasks import AgentTaskContext
from app.tools.registry import ToolRegistry


class DummyCreateTaskTool:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def execute(self, **kwargs):
        payload = kwargs["payload"]
        self.calls.append(payload)
        return {"id": "task-001", **payload}


class WorkAgentTest(unittest.IsolatedAsyncioTestCase):
    async def test_should_persist_task_when_actionable(self) -> None:
        registry = ToolRegistry()
        tool = DummyCreateTaskTool()
        registry.register("create_task", tool)
        agent = WorkAgent(registry)

        event = UnifiedEvent(
            eventId="qq:message:group:task-123",
            platform="qq",
            scene="work",
            eventType="message",
            chatType="group",
            chatId="1098307542",
            sender=Sender(id="2597164807", name="freeze", role="owner"),
            text="Please finish the project report tomorrow 18:00 and submit it to the group.",
            attachments=[],
            mentions=[],
            timestamp="2026-07-07T10:00:00+08:00",
            rawPayload={},
        )

        context = AgentTaskContext(
            task_id="task-run-001",
            route="task_plan",
            event=event,
            allowed_tools=["create_task"],
        )

        result = await agent.run(context, "build_work_plan")

        self.assertEqual(result.agent, "work")
        self.assertTrue(result.structured_result["actionable"])
        self.assertEqual(result.structured_result["priority"], "medium")
        self.assertIn("persisted_task", result.structured_result)
        self.assertEqual(len(tool.calls), 1)
        self.assertEqual(tool.calls[0]["sourceEventId"], "qq:message:group:task-123")
        self.assertEqual(tool.calls[0]["status"], "pending")


if __name__ == "__main__":
    unittest.main()
