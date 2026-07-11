from __future__ import annotations

import unittest

from app.agents.schedule_agent import ScheduleAgent
from app.schemas.events import Sender, UnifiedEvent
from app.schemas.tasks import AgentTaskContext
from app.tools.registry import ToolRegistry


class DummyCreateScheduleTool:
    def __init__(self) -> None:
        # 这个构造函数的作用是记录 ScheduleAgent 的落库调用参数。
        self.calls: list[dict] = []

    async def execute(self, **kwargs):
        # 这个函数的作用是模拟日程落库成功并返回伪造结果。
        payload = kwargs["payload"]
        self.calls.append(payload)
        return {"id": "schedule-001", **payload}


class DummyLlmClient:
    def __init__(self) -> None:
        # 这个构造函数的作用是记录 LLM 调用情况，供测试断言使用。
        self.calls: list[dict] = []

    def is_enabled(self, model_profile=None) -> bool:
        # 这个函数的作用是模拟当前运行时已经配置好了可用模型。
        return model_profile is not None

    async def generate_reply(self, system_prompt, user_message, temperature=0.7, model_profile=None):
        # 这个函数的作用是返回固定的模型回复，避免单测依赖真实外部模型。
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "user_message": user_message,
                "temperature": temperature,
                "model_profile": model_profile,
            }
        )
        return "这是模型生成的日程确认回复"


class ScheduleAgentTest(unittest.IsolatedAsyncioTestCase):
    async def test_should_persist_schedule_when_start_time_exists(self) -> None:
        # 这个测试函数的作用是验证 ScheduleAgent 能提取并持久化日程。
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

    async def test_should_use_llm_reply_when_model_profile_exists(self) -> None:
        # 这个测试函数的作用是验证 ScheduleAgent 在命中用户模型配置时会优先使用大模型回复。
        registry = ToolRegistry()
        tool = DummyCreateScheduleTool()
        registry.register("create_schedule", tool)
        llm_client = DummyLlmClient()
        agent = ScheduleAgent(registry, llm_client=llm_client)

        event = UnifiedEvent(
            eventId="qq:message:group:1843661134",
            platform="qq",
            scene="life",
            eventType="message",
            chatType="group",
            chatId="138178088",
            sender=Sender(id="2597164807", name="freeze", role="owner"),
            text="【项目例会】今天14:00在A01-N105开会。",
            attachments=[],
            mentions=[],
            timestamp="2026-07-06T12:00:00+08:00",
            rawPayload={},
        )

        context = AgentTaskContext(
            task_id="task-002",
            route="schedule_extract",
            event=event,
            allowed_tools=["create_schedule"],
            metadata={
                "conversation_profile_match": {
                    "matched": True,
                    "active": True,
                    "profile": {
                        "systemPrompt": "你要像细致的会议助理一样确认时间地点，并保持口吻利落。",
                        "skillReferences": ["skills/schedule/meeting-assistant"],
                        "modelProfileId": "profile-001",
                        "allowedTools": ["create_schedule"],
                    },
                },
                "resolved_model_profile": {
                    "matched": True,
                    "profile": {
                        "id": "profile-001",
                        "userId": "freeze",
                        "name": "日程模型",
                        "provider": "OPENAI_COMPATIBLE",
                        "baseUrl": "https://example.com/v1",
                        "apiKey": "sk-test",
                        "model": "gpt-4o-mini",
                        "temperature": 0.2,
                        "maxTokens": 1024,
                        "supportedRoutes": ["schedule_extract"],
                        "isDefault": True,
                        "priority": 10,
                    },
                }
            },
        )

        result = await agent.run(context, "extract_schedule")

        self.assertEqual(result.reply_draft, "这是模型生成的日程确认回复")
        self.assertTrue(result.structured_result["llmEnabled"])
        self.assertTrue(result.structured_result["llmUsed"])
        self.assertEqual(len(llm_client.calls), 1)
        self.assertIn("当前会话已绑定以下 skills", llm_client.calls[0]["system_prompt"])
        self.assertIn("会议助理", llm_client.calls[0]["system_prompt"])
        self.assertEqual(result.structured_result["promptSource"], "skill_plus_profile_prompt")
        self.assertEqual(result.structured_result["modelProfileId"], "profile-001")

    async def test_should_block_schedule_persistence_when_tool_is_not_allowed(self) -> None:
        # 这个测试函数的作用是验证会话工具白名单未放行 create_schedule 时，ScheduleAgent 不会继续落库。
        registry = ToolRegistry()
        tool = DummyCreateScheduleTool()
        registry.register("create_schedule", tool)
        agent = ScheduleAgent(registry)

        event = UnifiedEvent(
            eventId="qq:message:group:1843661135",
            platform="qq",
            scene="life",
            eventType="message",
            chatType="group",
            chatId="138178088",
            sender=Sender(id="2597164807", name="freeze", role="owner"),
            text="【项目例会】今天14:00在A01-N105开会。",
            attachments=[],
            mentions=[],
            timestamp="2026-07-06T12:30:00+08:00",
            rawPayload={},
        )

        context = AgentTaskContext(
            task_id="task-003",
            route="schedule_extract",
            event=event,
            allowed_tools=["send_qq_message"],
            metadata={
                "conversation_profile_match": {
                    "matched": True,
                    "active": True,
                    "profile": {
                        "allowedTools": ["send_qq_message"],
                    },
                }
            },
        )

        result = await agent.run(context, "extract_schedule")

        self.assertEqual(len(tool.calls), 0)
        self.assertIn("create_schedule tool is not allowed", result.next_actions)
        self.assertNotIn("persisted_schedule", result.structured_result)


if __name__ == "__main__":
    unittest.main()
