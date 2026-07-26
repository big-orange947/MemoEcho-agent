from __future__ import annotations

import unittest

from app.agents.schedule_agent import ScheduleAgent
from app.schemas.events import Sender, UnifiedEvent
from app.schemas.tasks import AgentTaskContext
from app.tools.registry import ToolRegistry
from tool_test_utils import register_test_tool


class RecordingScheduleTool:
    """记录测试期间真正发生的日程写入，便于确认阻断逻辑没有副作用。"""

    def __init__(self) -> None:
        # 这个构造函数的作用是初始化空调用记录。
        self.calls: list[dict] = []

    async def execute(self, **kwargs):
        # 这个函数的作用是模拟 Java 日程服务成功写入，并保存收到的 payload。
        payload = kwargs["payload"]
        self.calls.append(payload)
        return {"id": "schedule-pipeline-001", **payload}


class StructuredLlmClient:
    """按提示词类型分别返回结构化 JSON 和自然语言确认，避免单测访问真实模型。"""

    def __init__(self, structured_response: str) -> None:
        # 这个构造函数的作用是保存预期结构化结果和调用记录。
        self.structured_response = structured_response
        self.calls: list[dict] = []

    def is_enabled(self, model_profile=None) -> bool:
        # 这个函数的作用是模拟已经配置可用的用户模型。
        return model_profile is not None

    async def generate_reply(self, system_prompt, user_message, temperature=0.7, model_profile=None):
        # 这个函数的作用是让同一客户端同时覆盖抽取和反馈两个模型调用阶段。
        self.calls.append({"system_prompt": system_prompt, "user_message": user_message})
        if "结构化日程抽取器" in system_prompt:
            return self.structured_response
        return "已识别并记录这条日程"


def build_event(text: str, event_id: str = "qq:schedule:pipeline") -> UnifiedEvent:
    # 这个函数的作用是构造拥有固定参考时间的测试事件，确保相对时间断言可重复。
    return UnifiedEvent(
        eventId=event_id,
        platform="qq",
        scene="life",
        eventType="message",
        chatType="private",
        chatId="10001",
        sender=Sender(id="10001", name="friend", role=None),
        text=text,
        attachments=[],
        mentions=[],
        timestamp="2026-07-06T12:00:00+08:00",
        rawPayload={},
    )


def build_model_metadata(require_confirmation: bool = False) -> dict:
    # 这个函数的作用是生成最小可用模型配置，并按需开启会话级人工确认。
    return {
        "conversation_profile_match": {
            "matched": True,
            "active": True,
            "profile": {
                "requireHumanConfirmation": require_confirmation,
                "allowedTools": ["create_schedule"],
            },
        },
        "resolved_model_profile": {
            "matched": True,
            "profile": {
                "id": "profile-schedule",
                "userId": "freeze",
                "name": "日程模型",
                "provider": "OPENAI_COMPATIBLE",
                "baseUrl": "https://example.com/v1",
                "apiKey": "sk-test",
                "model": "test-model",
                "supportedRoutes": ["schedule_extract"],
            },
        },
    }


class ScheduleExtractionPipelineTest(unittest.IsolatedAsyncioTestCase):
    async def test_should_not_create_schedule_for_query(self) -> None:
        # 这个测试函数的作用是验证“查看日程”请求不会被误当成新增日程。
        registry = ToolRegistry()
        tool = RecordingScheduleTool()
        register_test_tool(registry, "create_schedule", tool)
        agent = ScheduleAgent(registry)
        context = AgentTaskContext(
            task_id="query-task",
            route="schedule_extract",
            event=build_event("我今天有什么日程安排吗"),
            allowed_tools=["create_schedule"],
        )

        result = await agent.run(context, "extract_schedule")

        self.assertEqual(result.structured_result["intent"], "QUERY")
        self.assertEqual(result.structured_result["candidateStatus"], "REJECTED")
        self.assertEqual(tool.calls, [])

    async def test_should_not_create_cancelled_or_past_schedule(self) -> None:
        # 这个测试函数的作用是验证取消表达和过去时间都无法触发 create_schedule。
        registry = ToolRegistry()
        tool = RecordingScheduleTool()
        register_test_tool(registry, "create_schedule", tool)
        agent = ScheduleAgent(registry)

        cancelled = AgentTaskContext(
            task_id="cancel-task",
            route="schedule_extract",
            event=build_event("不用提醒我明天14:00开会", "qq:schedule:cancel"),
            allowed_tools=["create_schedule"],
        )
        past = AgentTaskContext(
            task_id="past-task",
            route="schedule_extract",
            event=build_event("【旧会议】2026年7月5日14:00开会", "qq:schedule:past"),
            allowed_tools=["create_schedule"],
        )

        cancelled_result = await agent.run(cancelled, "extract_schedule")
        past_result = await agent.run(past, "extract_schedule")

        self.assertEqual(cancelled_result.structured_result["intent"], "CANCEL")
        self.assertEqual(past_result.structured_result["candidateStatus"], "DRAFT")
        self.assertIn("start_time_is_in_the_past", past_result.structured_result["validationErrors"])
        self.assertEqual(tool.calls, [])

    async def test_should_not_create_update_or_ambiguous_multi_event(self) -> None:
        # 这个测试函数的作用是验证改期请求和包含多个日程的消息都只能形成待处理候选。
        registry = ToolRegistry()
        tool = RecordingScheduleTool()
        register_test_tool(registry, "create_schedule", tool)
        agent = ScheduleAgent(registry)

        update_context = AgentTaskContext(
            task_id="update-task",
            route="schedule_extract",
            event=build_event("把明天14:00的会议改到后天15:00", "qq:schedule:update"),
            allowed_tools=["create_schedule"],
        )
        multiple_context = AgentTaskContext(
            task_id="multiple-task",
            route="schedule_extract",
            event=build_event(
                "2026年7月20日14:00开会，2026年7月21日15:00复盘",
                "qq:schedule:multiple",
            ),
            allowed_tools=["create_schedule"],
        )

        update_result = await agent.run(update_context, "extract_schedule")
        multiple_result = await agent.run(multiple_context, "extract_schedule")

        self.assertEqual(update_result.structured_result["intent"], "UPDATE")
        self.assertEqual(update_result.structured_result["candidateStatus"], "DRAFT")
        self.assertEqual(multiple_result.structured_result["candidateStatus"], "NEEDS_CLARIFICATION")
        self.assertEqual(tool.calls, [])

    async def test_should_use_grounded_llm_time_for_complex_expression(self) -> None:
        # 这个测试函数的作用是验证规则无法解析的相对时间可由 LLM 提交候选，但必须带有原文证据。
        response = (
            '{"intent":"CREATE","negated":false,"confidence":0.93,"events":['
            '{"title":"客户回访","dateText":"两小时后","startTimeText":"两小时后",'
            '"endTimeText":"","normalizedStartTime":"2026-07-06T14:00:00+08:00",'
            '"normalizedEndTime":"","location":"","participants":["客户"],'
            '"evidence":["两小时后提醒我给客户回电话"],"missingFields":[],"confidence":0.92}]}'
        )
        llm_client = StructuredLlmClient(response)
        registry = ToolRegistry()
        tool = RecordingScheduleTool()
        register_test_tool(registry, "create_schedule", tool)
        agent = ScheduleAgent(registry, llm_client=llm_client)
        context = AgentTaskContext(
            task_id="llm-structured-task",
            route="schedule_extract",
            event=build_event("两小时后提醒我给客户回电话"),
            allowed_tools=["create_schedule"],
            metadata=build_model_metadata(),
        )

        result = await agent.run(context, "extract_schedule")

        self.assertEqual(result.structured_result["start_time"], "2026-07-06 14:00:00")
        self.assertEqual(result.structured_result["candidateStatus"], "CONFIRMED")
        self.assertTrue(result.structured_result["structuredExtractionUsed"])
        self.assertEqual(len(tool.calls), 1)
        self.assertEqual(len(llm_client.calls), 2)

    async def test_should_wait_for_confirmation_before_persistence(self) -> None:
        # 这个测试函数的作用是验证会话要求人工确认时，即使候选完全明确也不能先产生落库副作用。
        registry = ToolRegistry()
        tool = RecordingScheduleTool()
        register_test_tool(registry, "create_schedule", tool)
        agent = ScheduleAgent(registry)
        context = AgentTaskContext(
            task_id="confirmation-task",
            route="schedule_extract",
            event=build_event("【项目例会】明天14:00在A01-N105开会"),
            allowed_tools=["create_schedule"],
            metadata=build_model_metadata(require_confirmation=True),
        )

        result = await agent.run(context, "extract_schedule")

        self.assertEqual(result.structured_result["persistence_status"], "awaiting_confirmation")
        self.assertTrue(result.need_confirmation)
        self.assertEqual(tool.calls, [])

    async def test_should_persist_open_ended_activity_when_semantic_gate_confirms_create(self) -> None:
        # 这个测试函数的作用是验证活动名称不在关键词表时，语义门控和明确日期时间仍能共同形成可落库日程。
        registry = ToolRegistry()
        tool = RecordingScheduleTool()
        register_test_tool(registry, "create_schedule", tool)
        agent = ScheduleAgent(registry)
        context = AgentTaskContext(
            task_id="semantic-rule-task",
            route="schedule_extract",
            event=build_event(
                "后天上午九点到十一点在实验室进行设备测试",
                "qq:schedule:semantic-rule",
            ),
            allowed_tools=["create_schedule"],
            metadata={
                "semantic_schedule_intent": {
                    "label": "schedule_create",
                    "route": "schedule_extract",
                    "score": 0.67,
                    "margin": 0.13,
                    "decisive": True,
                }
            },
        )

        result = await agent.run(context, "extract_schedule")

        self.assertEqual(result.structured_result["intent"], "CREATE")
        self.assertEqual(result.structured_result["candidateStatus"], "CONFIRMED")
        self.assertEqual(result.structured_result["start_time"], "2026-07-08 09:00:00")
        self.assertEqual(result.structured_result["end_time"], "2026-07-08 11:00:00")
        self.assertEqual(result.structured_result["location"], "实验室")
        self.assertEqual(len(tool.calls), 1)


if __name__ == "__main__":
    unittest.main()
