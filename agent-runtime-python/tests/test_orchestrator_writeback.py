from __future__ import annotations

import unittest

from app.memory.manager import MemoryManager
from app.orchestrator.service import OrchestratorService
from app.planner.service import PlannerService
from app.router.service import RouterService
from app.schemas.events import Sender, UnifiedEvent
from app.schemas.profiles import ConversationProfile, ConversationProfileMatchResult
from app.services.slow_channel_buffer import SlowChannelBuffer
from app.tools.registry import ToolRegistry


class DummySendQqMessageTool:
    def __init__(self) -> None:
        # 这个构造函数的作用是记录回写 QQ 的参数，便于测试断言。
        self.calls: list[dict] = []

    async def execute(self, **kwargs):
        # 这个函数的作用是模拟 QQ 发送成功。
        self.calls.append(kwargs)
        return {"status": "ok"}


class DummyExtractFileTextTool:
    def __init__(self) -> None:
        # 这个构造函数的作用是记录附件提取调用，并返回稳定的假数据供编排测试使用。
        self.calls: list[dict] = []

    async def execute(self, **kwargs):
        # 这个函数的作用是模拟附件分析结果，让 Orchestrator 能继续驱动 WorkAgent。
        self.calls.append(kwargs)
        return {
            "source": "attachment_metadata",
            "attachment_count": 1,
            "attachment_names": ["项目报告_2026-07-10 18:00.docx"],
            "extracted_text": "附件信息：请在 2026-07-10 18:00 前完成并提交项目报告",
        }


class DummyCreateTaskTool:
    def __init__(self) -> None:
        # 这个构造函数的作用是记录任务落库请求，验证附件链路是否真的走到了 WorkAgent。
        self.calls: list[dict] = []

    async def execute(self, **kwargs):
        # 这个函数的作用是模拟任务持久化成功。
        payload = kwargs["payload"]
        self.calls.append(payload)
        return {"id": "task-001", **payload}


class DummyListTasksTool:
    def __init__(self) -> None:
        # 这个构造函数的作用是记录任务查询请求，并返回固定待办列表。
        self.calls: list[dict] = []

    async def execute(self, **kwargs):
        # 这个函数的作用是模拟 task-service 查询结果，供 Orchestrator 回写今日计划。
        params = kwargs["params"]
        self.calls.append(params)
        return [
            {
                "id": "task-001",
                "title": "完成项目周报",
                "description": "整理进展后同步到群里",
                "priority": "high",
                "status": "pending",
                "dueTime": "2026-07-07 18:00:00",
                "createdAt": "2026-07-07 09:00:00",
            }
        ]


class DummyEventCenterServiceClient:
    def __init__(self, match_result: ConversationProfileMatchResult | None = None) -> None:
        # 这个构造函数的作用是预置设定集匹配结果，便于测试不同自动回复策略。
        self.match_result = match_result
        self.calls: list[dict] = []
        self.model_calls: list[dict] = []

    async def match_conversation_profile(self, event: UnifiedEvent, route: str) -> ConversationProfileMatchResult | None:
        # 这个函数的作用是模拟设定集匹配接口，并记录参与匹配的消息和预判 route。
        self.calls.append({"eventId": event.event_id, "chatId": event.chat_id, "route": route})
        return self.match_result

    async def resolve_user_model_profile(self, route: str, user_id: str | None = None, profile_id: str | None = None):
        # 这个函数的作用是模拟模型配置解析接口；当前这组编排测试不关心模型细节，直接返回未命中。
        self.model_calls.append({"route": route, "userId": user_id, "profileId": profile_id})
        return None


class DummySleeper:
    def __init__(self) -> None:
        # 这个构造函数的作用是记录延迟回复请求，避免单测真的等待。
        self.calls: list[int] = []

    async def __call__(self, seconds: int) -> None:
        # 这个函数的作用是接收 orchestrator 的延迟发送请求并只记录不等待。
        self.calls.append(seconds)


class OrchestratorWriteBackTest(unittest.IsolatedAsyncioTestCase):
    async def test_should_resolve_model_with_desktop_event_user(self) -> None:
        # 这个测试函数的作用是验证桌面命令使用当前登录用户的模型配置，而不是共享默认环境用户。
        event_center_client = DummyEventCenterServiceClient()
        service = OrchestratorService(
            router=RouterService(),
            planner=PlannerService(),
            tools=ToolRegistry(),
            memory=MemoryManager(),
            slow_channel_buffer=SlowChannelBuffer(window_seconds=600, max_messages=10),
            event_center_client=event_center_client,
        )
        event = UnifiedEvent(
            eventId="desktop:command:1",
            platform="desktop",
            scene="workspace",
            eventType="desktop_command",
            chatType="private",
            chatId="workspace:user-001",
            sender=Sender(id="user-001", name="freeze", role="owner"),
            text="我今天该做什么？",
            attachments=[],
            mentions=[],
            timestamp="2026-07-11T10:00:00+08:00",
            rawPayload={"userId": "user-001", "requestedRoute": "task_plan"},
        )

        await service._resolve_user_model_profile(event, "task_plan", None)

        self.assertEqual(event_center_client.model_calls[0]["userId"], "user-001")

    async def test_should_intersect_profile_tools_with_skill_tool_policy(self) -> None:
        # 这个测试函数的作用是验证会话 profile 的工具白名单会和 skill 自带的工具白名单取交集，避免暴露越权工具。
        tools = ToolRegistry()
        tools.register("send_qq_message", DummySendQqMessageTool())
        tools.register("create_task", DummyCreateTaskTool())
        tools.register("list_tasks", DummyListTasksTool())
        service = OrchestratorService(
            router=RouterService(),
            planner=PlannerService(),
            tools=tools,
            memory=MemoryManager(),
            slow_channel_buffer=SlowChannelBuffer(window_seconds=600, max_messages=10),
            event_center_client=DummyEventCenterServiceClient(),
        )

        profile_match = ConversationProfileMatchResult(
            matched=True,
            active=True,
            reason="命中工作会话设定",
            profile=ConversationProfile(
                id="profile-tools-001",
                name="任务规划会话",
                preferredRoute="task_plan",
                allowedTools=["send_qq_message", "create_task"],
                skillReferences=["skills/work/project-manager"],
            ),
        )

        resolved_skills, unresolved_references = service._resolve_skills(profile_match, "task_plan")
        allowed_tools = service._resolve_allowed_tools(profile_match, resolved_skills)

        self.assertEqual(unresolved_references, [])
        self.assertEqual(len(resolved_skills), 1)
        self.assertEqual(resolved_skills[0].id, "work.project_manager")
        self.assertEqual(allowed_tools, ["create_task"])

    async def test_should_send_group_summary_when_slow_channel_flushes(self) -> None:
        # 这个测试函数的作用是验证普通群消息在慢通道触发汇总时会回写群摘要。
        tools = ToolRegistry()
        send_tool = DummySendQqMessageTool()
        tools.register("send_qq_message", send_tool)
        service = OrchestratorService(
            router=RouterService(),
            planner=PlannerService(),
            tools=tools,
            memory=MemoryManager(),
            slow_channel_buffer=SlowChannelBuffer(window_seconds=600, max_messages=1),
            event_center_client=DummyEventCenterServiceClient(),
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
        # 这个测试函数的作用是验证被 @ 后生成的回复会以 at + 文本分段形式回写群聊。
        tools = ToolRegistry()
        send_tool = DummySendQqMessageTool()
        tools.register("send_qq_message", send_tool)
        service = OrchestratorService(
            router=RouterService(),
            planner=PlannerService(),
            tools=tools,
            memory=MemoryManager(),
            slow_channel_buffer=SlowChannelBuffer(window_seconds=600, max_messages=10),
            event_center_client=DummyEventCenterServiceClient(),
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

    async def test_should_run_file_analysis_flow_and_write_back_daily_plan_to_private_chat(self) -> None:
        # 这个测试函数的作用是验证附件分析链路最终会把工作计划回写到私聊。
        tools = ToolRegistry()
        send_tool = DummySendQqMessageTool()
        extract_tool = DummyExtractFileTextTool()
        create_task_tool = DummyCreateTaskTool()
        tools.register("send_qq_message", send_tool)
        tools.register("extract_file_text", extract_tool)
        tools.register("create_task", create_task_tool)
        service = OrchestratorService(
            router=RouterService(),
            planner=PlannerService(),
            tools=tools,
            memory=MemoryManager(),
            slow_channel_buffer=SlowChannelBuffer(window_seconds=600, max_messages=10),
            event_center_client=DummyEventCenterServiceClient(),
        )

        event = UnifiedEvent(
            eventId="qq:message:private:30003",
            platform="qq",
            scene="work",
            eventType="message",
            chatType="private",
            chatId="2597164807",
            selfId="3969785168",
            sender=Sender(id="2597164807", name="freeze", role=None),
            text="请根据附件整理任务并提醒我",
            attachments=[{"fileId": "f-1", "fileName": "项目报告_2026-07-10 18:00.docx", "fileType": "file"}],
            mentions=[],
            timestamp="2026-07-07T00:25:00+08:00",
            rawPayload={"self_id": 3969785168},
        )

        result = await service.handle_event(event)

        self.assertEqual(result.route, "file_analysis")
        self.assertEqual(len(extract_tool.calls), 1)
        self.assertEqual(len(create_task_tool.calls), 1)
        self.assertEqual(len(send_tool.calls), 1)
        self.assertEqual(send_tool.calls[0]["chat_type"], "private")
        self.assertIn("今天的工作计划", send_tool.calls[0]["message"])
        self.assertIn("建议步骤", send_tool.calls[0]["message"])
        self.assertIn("qq_write_back_sent:ok", result.write_back_actions)

    async def test_should_query_existing_tasks_and_write_back_today_plan_to_private_chat(self) -> None:
        # 这个测试函数的作用是验证“我今天该做什么”会走任务查询链路并把结果回写到私聊。
        tools = ToolRegistry()
        send_tool = DummySendQqMessageTool()
        list_tool = DummyListTasksTool()
        tools.register("send_qq_message", send_tool)
        tools.register("list_tasks", list_tool)
        service = OrchestratorService(
            router=RouterService(),
            planner=PlannerService(),
            tools=tools,
            memory=MemoryManager(),
            slow_channel_buffer=SlowChannelBuffer(window_seconds=600, max_messages=10),
            event_center_client=DummyEventCenterServiceClient(),
        )

        event = UnifiedEvent(
            eventId="qq:message:private:30004",
            platform="qq",
            scene="work",
            eventType="message",
            chatType="private",
            chatId="2597164807",
            selfId="3969785168",
            sender=Sender(id="2597164807", name="freeze", role=None),
            text="我今天该做什么？",
            attachments=[],
            mentions=[],
            timestamp="2026-07-07T00:30:00+08:00",
            rawPayload={"self_id": 3969785168},
        )

        result = await service.handle_event(event)

        self.assertEqual(result.route, "task_plan")
        self.assertEqual(len(list_tool.calls), 1)
        self.assertEqual(len(send_tool.calls), 1)
        self.assertEqual(send_tool.calls[0]["chat_type"], "private")
        self.assertIn("当前共查到 1 条待办", send_tool.calls[0]["message"])
        self.assertIn("完成项目周报", send_tool.calls[0]["message"])
        self.assertIn("qq_write_back_sent:ok", result.write_back_actions)

    async def test_should_not_auto_write_back_when_profile_requires_draft_only(self) -> None:
        # 这个测试函数的作用是验证命中“只生成草稿”的设定后，私聊也不会自动回写到 QQ。
        tools = ToolRegistry()
        send_tool = DummySendQqMessageTool()
        tools.register("send_qq_message", send_tool)
        profile_match = ConversationProfileMatchResult(
            matched=True,
            active=True,
            reason="命中会话范围且满足触发条件",
            profile=ConversationProfile(
                id="profile-001",
                name="只出草稿",
                replyMode="DRAFT_ONLY",
                preferredRoute="social_reply",
            ),
        )
        service = OrchestratorService(
            router=RouterService(),
            planner=PlannerService(),
            tools=tools,
            memory=MemoryManager(),
            slow_channel_buffer=SlowChannelBuffer(window_seconds=600, max_messages=10),
            event_center_client=DummyEventCenterServiceClient(profile_match),
        )

        event = UnifiedEvent(
            eventId="qq:message:private:30005",
            platform="qq",
            scene="life",
            eventType="message",
            chatType="private",
            chatId="2597164807",
            selfId="3969785168",
            sender=Sender(id="2597164807", name="freeze", role=None),
            text="在吗",
            attachments=[],
            mentions=[],
            timestamp="2026-07-07T00:31:00+08:00",
            rawPayload={"self_id": 3969785168},
        )

        result = await service.handle_event(event)

        self.assertEqual(result.route, "social_reply")
        self.assertEqual(len(send_tool.calls), 0)
        self.assertEqual(result.write_back_actions, ["qq_write_back_skipped:draft_only"])

    async def test_should_delay_auto_reply_when_profile_configures_reply_window(self) -> None:
        # 这个测试函数的作用是验证命中延迟回复策略后，orchestrator 会先执行延迟再发送消息。
        tools = ToolRegistry()
        send_tool = DummySendQqMessageTool()
        sleeper = DummySleeper()
        tools.register("send_qq_message", send_tool)
        profile_match = ConversationProfileMatchResult(
            matched=True,
            active=True,
            reason="命中会话范围且满足触发条件",
            profile=ConversationProfile(
                id="profile-002",
                name="延迟私聊回复",
                personaMode="PROMPT",
                systemPrompt="你要自然回复。",
                replyMode="AUTO_REPLY",
                preferredRoute="social_reply",
                replyDelaySecondsMin=2,
                replyDelaySecondsMax=2,
            ),
        )
        service = OrchestratorService(
            router=RouterService(),
            planner=PlannerService(),
            tools=tools,
            memory=MemoryManager(),
            slow_channel_buffer=SlowChannelBuffer(window_seconds=600, max_messages=10),
            event_center_client=DummyEventCenterServiceClient(profile_match),
            sleeper=sleeper,
        )

        event = UnifiedEvent(
            eventId="qq:message:private:30006",
            platform="qq",
            scene="life",
            eventType="message",
            chatType="private",
            chatId="2597164807",
            selfId="3969785168",
            sender=Sender(id="2597164807", name="freeze", role=None),
            text="在吗",
            attachments=[],
            mentions=[],
            timestamp="2026-07-07T00:32:00+08:00",
            rawPayload={"self_id": 3969785168},
        )

        result = await service.handle_event(event)

        self.assertEqual(result.route, "social_reply")
        self.assertEqual(sleeper.calls, [2])
        self.assertEqual(len(send_tool.calls), 1)
        self.assertEqual(
            result.write_back_actions,
            ["qq_write_back_delayed:2s", "qq_write_back_sent:ok"],
        )


if __name__ == "__main__":
    unittest.main()
