from __future__ import annotations

from datetime import datetime, timedelta
import unittest

from app.agents.work_agent import WorkAgent
from app.schemas.events import Sender, UnifiedEvent
from app.schemas.tasks import AgentTaskContext
from app.tools.registry import ToolRegistry


class DummyCreateTaskTool:
    def __init__(self) -> None:
        # 这个构造函数的作用是记录任务持久化调用，供测试断言使用。
        self.calls: list[dict] = []

    async def execute(self, **kwargs):
        # 这个函数的作用是模拟任务落库成功，并返回带 id 的结果。
        payload = kwargs["payload"]
        self.calls.append(payload)
        return {"id": "task-001", **payload}


class DummyListTasksTool:
    def __init__(self) -> None:
        # 这个构造函数的作用是记录待办查询调用，并返回预置任务列表。
        self.calls: list[dict] = []

    async def execute(self, **kwargs):
        # 这个函数的作用是模拟待办查询结果，供 WorkAgent 生成今日计划。
        params = kwargs["params"]
        self.calls.append(params)

        now = datetime.now()
        today_due_time = now.replace(hour=18, minute=0, second=0, microsecond=0)
        upcoming_due_time = (now + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
        created_at_a = now.replace(hour=9, minute=0, second=0, microsecond=0)
        created_at_b = now.replace(hour=9, minute=10, second=0, microsecond=0)

        return [
            {
                "id": "task-a",
                "title": "完成项目周报",
                "description": "整理本周进展并发群里",
                "priority": "high",
                "status": "pending",
                "dueTime": today_due_time.strftime("%Y-%m-%d %H:%M:%S"),
                "createdAt": created_at_a.strftime("%Y-%m-%d %H:%M:%S"),
            },
            {
                "id": "task-b",
                "title": "准备演示稿",
                "description": "补充 demo 截图和流程图",
                "priority": "medium",
                "status": "pending",
                "dueTime": upcoming_due_time.strftime("%Y-%m-%d %H:%M:%S"),
                "createdAt": created_at_b.strftime("%Y-%m-%d %H:%M:%S"),
            },
        ]


class DummyLlmClient:
    def __init__(self) -> None:
        # 这个构造函数的作用是记录模型调用参数，方便断言 WorkAgent 是否真的走了模型分支。
        self.calls: list[dict] = []

    def is_enabled(self, model_profile=None) -> bool:
        # 这个函数的作用是模拟当命中用户模型配置时，运行时可调用大模型。
        return model_profile is not None

    async def generate_reply(self, system_prompt, user_message, temperature=0.7, model_profile=None):
        # 这个函数的作用是返回固定回复，避免单测依赖真实模型。
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "user_message": user_message,
                "temperature": temperature,
                "model_profile": model_profile,
            }
        )
        return "这是模型生成的工作计划回复"


class WorkAgentTest(unittest.IsolatedAsyncioTestCase):
    async def test_should_persist_task_and_generate_daily_plan_when_actionable(self) -> None:
        # 这个测试函数的作用是验证 WorkAgent 在任务创建模式下会落库并生成今日计划。
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
        self.assertEqual(result.structured_result["mode"], "task_create")
        self.assertTrue(result.structured_result["actionable"])
        self.assertEqual(result.structured_result["priority"], "medium")
        self.assertIn("persisted_task", result.structured_result)
        self.assertIn("daily_plan", result.structured_result)
        self.assertIn("steps", result.structured_result["daily_plan"])
        self.assertGreaterEqual(len(result.structured_result["daily_plan"]["steps"]), 3)
        self.assertIn("今天的工作计划", result.reply_draft)
        self.assertEqual(len(tool.calls), 1)
        self.assertEqual(tool.calls[0]["sourceEventId"], "qq:message:group:task-123")
        self.assertEqual(tool.calls[0]["status"], "pending")

    async def test_should_use_file_analysis_result_when_present(self) -> None:
        # 这个测试函数的作用是验证 WorkAgent 会复用 FileAgent 的分析结果补强任务提取。
        registry = ToolRegistry()
        tool = DummyCreateTaskTool()
        registry.register("create_task", tool)
        agent = WorkAgent(registry)

        event = UnifiedEvent(
            eventId="qq:message:private:file-task-001",
            platform="qq",
            scene="work",
            eventType="message",
            chatType="private",
            chatId="2597164807",
            sender=Sender(id="2597164807", name="freeze", role="owner"),
            text="请根据附件整理任务",
            attachments=[],
            mentions=[],
            timestamp="2026-07-07T11:00:00+08:00",
            rawPayload={},
        )

        context = AgentTaskContext(
            task_id="task-run-002",
            route="file_analysis",
            event=event,
            allowed_tools=["create_task"],
            metadata={
                "previous_results": {
                    "file": {
                        "extracted_text": "附件信息：项目周报，2026-07-10 18:00 前提交最终版项目报告",
                    }
                }
            },
        )

        result = await agent.run(context, "build_work_plan")

        self.assertEqual(result.agent, "work")
        self.assertTrue(result.structured_result["actionable"])
        self.assertTrue(result.structured_result["used_file_analysis"])
        self.assertIn("persisted_task", result.structured_result)
        self.assertIn("2026-07-10 18:00:00", result.structured_result["due_time"])
        self.assertEqual(result.structured_result["daily_plan"]["mode"], "attachment_driven")
        self.assertIn("根据附件内容整理关键要求", "\n".join(result.structured_result["daily_plan"]["steps"]))
        self.assertEqual(len(tool.calls), 1)

    async def test_should_query_existing_tasks_and_generate_today_plan(self) -> None:
        # 这个测试函数的作用是验证 WorkAgent 在“我今天该做什么”场景下会改为读取已有待办。
        registry = ToolRegistry()
        create_tool = DummyCreateTaskTool()
        list_tool = DummyListTasksTool()
        registry.register("create_task", create_tool)
        registry.register("list_tasks", list_tool)
        agent = WorkAgent(registry)

        event = UnifiedEvent(
            eventId="qq:message:private:task-query-001",
            platform="qq",
            scene="work",
            eventType="message",
            chatType="private",
            chatId="2597164807",
            sender=Sender(id="2597164807", name="freeze", role="owner"),
            text="我今天该做什么？",
            attachments=[],
            mentions=[],
            timestamp="2026-07-07T13:00:00+08:00",
            rawPayload={},
        )

        context = AgentTaskContext(
            task_id="task-run-003",
            route="task_plan",
            event=event,
            allowed_tools=["create_task", "list_tasks"],
        )

        result = await agent.run(context, "build_work_plan")

        self.assertEqual(result.agent, "work")
        self.assertEqual(result.structured_result["mode"], "task_query")
        self.assertEqual(result.structured_result["task_count"], 2)
        self.assertEqual(result.structured_result["today_task_count"], 1)
        self.assertEqual(len(list_tool.calls), 1)
        self.assertEqual(list_tool.calls[0]["chatId"], "2597164807")
        self.assertTrue(list_tool.calls[0]["todayOnly"])
        self.assertEqual(list_tool.calls[0]["senderId"], "2597164807")
        self.assertEqual(len(create_tool.calls), 0)
        self.assertIn("当前共查到 2 条待办", result.reply_draft)
        self.assertIn("完成项目周报", result.reply_draft)

    async def test_should_use_llm_reply_when_resolved_model_profile_exists(self) -> None:
        # 这个测试函数的作用是验证 WorkAgent 在命中用户模型配置时会优先使用大模型回复。
        registry = ToolRegistry()
        tool = DummyCreateTaskTool()
        registry.register("create_task", tool)
        llm_client = DummyLlmClient()
        agent = WorkAgent(registry, llm_client=llm_client)

        event = UnifiedEvent(
            eventId="qq:message:group:task-456",
            platform="qq",
            scene="work",
            eventType="message",
            chatType="group",
            chatId="1098307542",
            sender=Sender(id="2597164807", name="freeze", role="owner"),
            text="请今天18:00前完成汇报材料并发给我。",
            attachments=[],
            mentions=[],
            timestamp="2026-07-07T14:00:00+08:00",
            rawPayload={},
        )

        context = AgentTaskContext(
            task_id="task-run-004",
            route="task_plan",
            event=event,
            allowed_tools=["create_task"],
            metadata={
                "conversation_profile_match": {
                    "matched": True,
                    "active": True,
                    "profile": {
                        "systemPrompt": "你要像项目经理一样给出清晰的执行顺序。",
                        "skillReferences": ["skills/work/project-manager"],
                        "modelProfileId": "profile-001",
                        "allowedTools": ["create_task"],
                    },
                },
                "resolved_model_profile": {
                    "matched": True,
                    "profile": {
                        "id": "profile-001",
                        "userId": "freeze",
                        "name": "工作模型",
                        "provider": "OPENAI_COMPATIBLE",
                        "baseUrl": "https://example.com/v1",
                        "apiKey": "sk-test",
                        "model": "gpt-4o-mini",
                        "temperature": 0.4,
                        "maxTokens": 1024,
                        "supportedRoutes": ["task_plan"],
                        "isDefault": True,
                        "priority": 10,
                    },
                }
            },
        )

        result = await agent.run(context, "build_work_plan")

        self.assertEqual(result.reply_draft, "这是模型生成的工作计划回复")
        self.assertTrue(result.structured_result["llmEnabled"])
        self.assertTrue(result.structured_result["llmUsed"])
        self.assertEqual(len(llm_client.calls), 1)
        self.assertIn("当前会话已绑定以下 skills", llm_client.calls[0]["system_prompt"])
        self.assertIn("项目经理", llm_client.calls[0]["system_prompt"])
        self.assertEqual(result.structured_result["promptSource"], "skill_plus_profile_prompt")
        self.assertEqual(result.structured_result["modelProfileId"], "profile-001")
        self.assertEqual(result.structured_result["allowedTools"], ["create_task"])

    async def test_should_block_task_creation_when_tool_is_not_allowed(self) -> None:
        # 这个测试函数的作用是验证会话工具白名单未放行 create_task 时，WorkAgent 不会继续落库。
        registry = ToolRegistry()
        tool = DummyCreateTaskTool()
        registry.register("create_task", tool)
        agent = WorkAgent(registry)

        event = UnifiedEvent(
            eventId="qq:message:group:task-789",
            platform="qq",
            scene="work",
            eventType="message",
            chatType="group",
            chatId="1098307542",
            sender=Sender(id="2597164807", name="freeze", role="owner"),
            text="请今天18:00前完成演示文档",
            attachments=[],
            mentions=[],
            timestamp="2026-07-07T15:00:00+08:00",
            rawPayload={},
        )

        context = AgentTaskContext(
            task_id="task-run-005",
            route="task_plan",
            event=event,
            allowed_tools=["list_tasks"],
            metadata={
                "conversation_profile_match": {
                    "matched": True,
                    "active": True,
                    "profile": {
                        "allowedTools": ["list_tasks"],
                    },
                }
            },
        )

        result = await agent.run(context, "build_work_plan")

        self.assertEqual(len(tool.calls), 0)
        self.assertIn("create_task tool is not allowed", result.next_actions)
        self.assertNotIn("persisted_task", result.structured_result)


if __name__ == "__main__":
    unittest.main()
