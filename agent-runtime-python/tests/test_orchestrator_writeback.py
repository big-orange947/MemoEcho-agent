from __future__ import annotations

import asyncio
import json
import unittest
from types import SimpleNamespace

from app.memory.manager import MemoryManager
from app.orchestrator.service import OrchestratorService
from app.planner.service import PlannerService
from app.router.service import RouterService
from app.schemas.events import Sender, UnifiedEvent
from app.schemas.delegated_tasks import (
    ConversationCandidate,
    DelegatedTaskActionDecision,
    DelegatedTaskCompileResponse,
)
from app.schemas.delegated_workflows import DelegatedWorkflowPlan, DelegatedWorkflowPlanStep
from app.schemas.model_profiles import ResolvedUserModelProfile, UserModelProfileResolveResult
from app.schemas.profiles import ConversationProfile, ConversationProfileMatchResult
from app.schemas.results import AgentResult
from app.services.slow_channel_buffer import SlowChannelBuffer
from app.tools.registry import ToolRegistry
from tool_test_utils import register_test_tool


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
        self.match_result = match_result or ConversationProfileMatchResult(
            matched=True,
            active=True,
            reason="测试默认允许自动回复",
            profile=ConversationProfile(
                id="test-auto-profile",
                name="测试自动回复设定",
                replyMode="AUTO_REPLY",
                requireHumanConfirmation=False,
            ),
        )
        self.calls: list[dict] = []
        self.model_calls: list[dict] = []
        self.active_delegated_task: dict | None = None
        self.delegated_runtime_updates: list[dict] = []
        self.delegated_event_claim_allowed = True
        self.delegated_event_claims: list[dict] = []
        self.delegated_event_completions: list[dict] = []
        self.delegated_event_releases: list[dict] = []
        self.delegated_workflow_completions: list[dict] = []
        self.delegated_workflow_completion_error: Exception | None = None
        self.current_events: dict[str, dict] = {}

    async def match_conversation_profile(self, event: UnifiedEvent, route: str) -> ConversationProfileMatchResult | None:
        # 这个函数的作用是模拟设定集匹配接口，并记录参与匹配的消息和预判 route。
        self.calls.append({"eventId": event.event_id, "chatId": event.chat_id, "route": route})
        return self.match_result

    async def resolve_user_model_profile(self, route: str, user_id: str | None = None, profile_id: str | None = None):
        # 这个函数的作用是模拟模型配置解析接口；当前这组编排测试不关心模型细节，直接返回未命中。
        self.model_calls.append({"route": route, "userId": user_id, "profileId": profile_id})
        return None

    async def get_active_delegated_task(self, event: UnifiedEvent) -> dict | None:
        """模拟从 Java 持久层恢复当前会话唯一的活动委托任务。"""
        return self.active_delegated_task

    async def list_conversation_messages(self, chat_id: str, **kwargs) -> list[dict]:
        """返回空的可信历史时间线，让测试聚焦任务恢复和状态回写。"""
        return []

    async def update_delegated_task_runtime(
        self,
        event: UnifiedEvent,
        task_id: str,
        **runtime_state,
    ) -> dict:
        """记录 LangGraph 状态回写，验证 Python 进程重启后进度仍可恢复。"""
        update = {
            "taskId": task_id,
            "eventId": event.event_id,
            "status": runtime_state.get("status"),
            "progressSummary": runtime_state.get("progress_summary"),
            "stateJson": runtime_state.get("state_json"),
        }
        self.delegated_runtime_updates.append(update)
        return update

    async def claim_delegated_task_event(
        self,
        event: UnifiedEvent,
        task_id: str,
        event_id: str,
        lease_seconds: int = 120,
    ) -> dict:
        """模拟持久层事件租约，验证同一事件只能由一个 Runtime 执行。"""
        claim = {
            "taskId": task_id,
            "eventId": event_id,
            "leaseSeconds": lease_seconds,
        }
        self.delegated_event_claims.append(claim)
        if not self.delegated_event_claim_allowed:
            return {
                "claimed": False,
                "claimToken": None,
                "status": "PROCESSING",
            }
        return {
            "claimed": True,
            "claimToken": f"claim:{task_id}:{event_id}",
            "status": "PROCESSING",
        }

    async def complete_delegated_task_event(
        self,
        event: UnifiedEvent,
        task_id: str,
        event_id: str,
        claim_token: str,
    ) -> None:
        """记录租约提交，确保外部副作用完成后事件不会被再次消费。"""
        self.delegated_event_completions.append(
            {
                "taskId": task_id,
                "eventId": event_id,
                "claimToken": claim_token,
            }
        )

    async def release_delegated_task_event(
        self,
        event: UnifiedEvent,
        task_id: str,
        event_id: str,
        claim_token: str,
    ) -> None:
        """记录租约释放，供持久化失败重试场景断言。"""
        self.delegated_event_releases.append(
            {
                "taskId": task_id,
                "eventId": event_id,
                "claimToken": claim_token,
            }
        )

    async def complete_delegated_workflow_step(
        self,
        event: UnifiedEvent,
        workflow_id: str,
        step_key: str,
        *,
        produced_facts: dict,
        result_summary: str,
        result: object,
        artifacts: list[dict] | None = None,
        source_event_id: str | None = None,
    ) -> dict:
        """模拟父工作流步骤完成接口，并支持注入回调异常验证消息重试。"""
        if self.delegated_workflow_completion_error is not None:
            raise self.delegated_workflow_completion_error
        completion = {
            "eventId": event.event_id,
            "workflowId": workflow_id,
            "stepKey": step_key,
            "producedFacts": produced_facts,
            "resultSummary": result_summary,
            "result": result,
            "artifacts": artifacts or [],
            "sourceEventId": source_event_id,
        }
        self.delegated_workflow_completions.append(completion)
        return {"id": workflow_id, "status": "RUNNING"}

    async def upsert_delegated_task_current_event(self, event: UnifiedEvent, task_id: str) -> dict:
        """模拟 L0 当前事件写入，记录最近一次入站事件供历史失败时兜底。"""
        stored = {
            "taskId": task_id,
            "eventId": event.event_id,
            "eventType": event.event_type,
            "text": event.text or "",
            "payload": event.model_dump(mode="json", by_alias=True),
        }
        self.current_events[task_id] = stored
        return stored

    async def get_delegated_task_current_event(self, event: UnifiedEvent, task_id: str) -> dict | None:
        """模拟 L0 当前事件读取。"""
        return self.current_events.get(task_id)


class DummyDelegatedTaskRuntimeTool:
    def __init__(self, event_center_client: DummyEventCenterServiceClient, status: str) -> None:
        # 这个构造函数的作用是把委托任务状态工具绑定到测试用 event-center 客户端。
        self.event_center_client = event_center_client
        self.status = status
        self.calls: list[dict] = []

    async def execute(self, **kwargs):
        # 这个函数的作用是模拟 LangChain 工具写回委托任务运行态。
        self.calls.append(kwargs)
        event = UnifiedEvent.model_validate(kwargs["event"])
        return await self.event_center_client.update_delegated_task_runtime(
            event,
            kwargs["task_id"],
            status=self.status,
            progress_summary=kwargs.get("progress_summary", ""),
            state_json=kwargs.get("state_json", "{}"),
            last_event_id=kwargs.get("last_event_id", event.event_id),
            completion_report=kwargs.get("completion_report", ""),
        )


def register_delegated_runtime_tools(
    tools: ToolRegistry,
    event_center_client: DummyEventCenterServiceClient,
) -> None:
    # 这个函数的作用是统一注册测试用委托任务状态工具，避免测试绕过 ToolRegistry。
    register_test_tool(
        tools,
        "update_delegated_task",
        DummyDelegatedTaskRuntimeTool(event_center_client, "ACTIVE"),
    )
    register_test_tool(
        tools,
        "complete_delegated_task",
        DummyDelegatedTaskRuntimeTool(event_center_client, "COMPLETED"),
    )


class DummyVisionModelEventCenterClient(DummyEventCenterServiceClient):
    """返回按 route 区分的模型配置，用于验证回复模型和视觉模型不会混用。"""

    async def resolve_user_model_profile(self, route: str, user_id: str | None = None, profile_id: str | None = None):
        """模拟 Event Center 的真实模型解析返回结构，并保留调用参数供断言。"""
        self.model_calls.append({"route": route, "userId": user_id, "profileId": profile_id})
        return UserModelProfileResolveResult(
            matched=True,
            profile=ResolvedUserModelProfile(
                id=f"{route}-model",
                userId="freeze",
                name=route,
                apiKey="key",
                model=f"{route}-model",
                supportedRoutes=[route] if route == "vision_analysis" else [],
            ),
        )


class DummyConversationVisionPreferredEventCenterClient(DummyEventCenterServiceClient):
    """模拟会话绑定 Qwen3-VL、但视觉 route 错误命中全局 DeepSeek 的真实配置。"""

    def __init__(self) -> None:
        # 这个构造函数的作用是让设定集显式绑定 Qwen，复现用户当前数据库里的模型优先级。
        super().__init__(ConversationProfileMatchResult(
            matched=True,
            active=True,
            reason="命中图片私聊设定",
            profile=ConversationProfile(
                id="qwen-private-profile",
                name="Qwen 图片私聊",
                replyMode="AUTO_REPLY",
                modelProfileId="qwen-conversation-model",
            ),
        ))

    async def resolve_user_model_profile(self, route: str, user_id: str | None = None, profile_id: str | None = None):
        """分别返回会话 Qwen 和未声明视觉 route 的默认 DeepSeek，供优先级测试使用。"""
        self.model_calls.append({"route": route, "userId": user_id, "profileId": profile_id})
        if route == "social_reply":
            return UserModelProfileResolveResult(
                matched=True,
                profile=ResolvedUserModelProfile(
                    id="qwen-conversation-model",
                    userId="freeze",
                    name="Qwen3-VL Flash",
                    apiKey="key",
                    model="qwen3-vl-flash",
                ),
            )
        return UserModelProfileResolveResult(
            matched=True,
            profile=ResolvedUserModelProfile(
                id="deepseek-default-model",
                userId="freeze",
                name="DeepSeek Chat",
                apiKey="key",
                model="deepseek-v4-pro",
                supportedRoutes=[],
                isDefault=True,
            ),
        )


class CaptureMediaAnalysisService:
    """记录主链路传入的模型配置，避免真实调用视觉模型。"""

    def __init__(self) -> None:
        self.model_profile = None

    async def analyze_event(self, event, model_profile=None):
        """保存已解包的模型配置，并返回一个已识别图片结果。"""
        self.model_profile = model_profile
        return [{"status": "VISION_ANALYZED", "summary": "图片内容已识别", "extractedText": "会议通知"}]


class DummySleeper:
    def __init__(self) -> None:
        # 这个构造函数的作用是记录延迟回复请求，避免单测真的等待。
        self.calls: list[int] = []

    async def __call__(self, seconds: int) -> None:
        # 这个函数的作用是接收 orchestrator 的延迟发送请求并只记录不等待。
        self.calls.append(seconds)


class SupersedingSleeper:
    """在旧消息的防抖窗口内模拟收到同一任务的新入站消息。"""

    def __init__(self, service: OrchestratorService, task_id: str, latest_event_id: str) -> None:
        # 这个构造函数的作用是保存需要覆盖的任务版本，模拟真实并发入站事件。
        self.service = service
        self.task_id = task_id
        self.latest_event_id = latest_event_id

    async def __call__(self, seconds: int) -> None:
        # 这个函数的作用是在旧事件等待发送前推进版本号，使旧轮推理失效。
        for conversation_key in tuple(self.service._delegated_conversation_versions):
            self.service._delegated_conversation_versions[conversation_key] = (
                self.service._delegated_conversation_versions.get(conversation_key, 0) + 1
            )
            self.service._delegated_conversation_latest_event_ids[conversation_key] = self.latest_event_id


class DummyApprovedSocialLlm:
    """同时模拟社交回复模型和强制审批模型，审批步骤明确返回 APPROVE。"""

    def is_enabled(self, model_profile=None):
        return True

    async def generate_reply(self, system_prompt, user_message, temperature=0.7, model_profile=None):
        # 委托动作现在会在发送前执行完成复核；该测试场景仍需等待联系人最终答复。
        if "COMPLETION_REFLECTION" in system_prompt:
            return (
                '{"shouldComplete":false,"outcome":"SUCCESS",'
                '"reason":"联系人只提出了候选时间，尚未形成最终安排",'
                '"progressSummary":"正在协商时间","completionReport":"",'
                '"finalMessageInstruction":"","knownFacts":["联系人提出下午"],'
                '"pendingConditions":["等待双方确认具体时间"],"evidence":[],"evidenceEventIds":[]}'
            )
        if "主控台的任务执行规划器" in system_prompt:
            # 规划器必须显式请求工具，测试不能依赖旧版字符串兜底自动发送。
            return (
                '{"tool":"send_qq_message","candidateMessage":"老师您好，想确认一下明天课程时间",'
                '"reason":"任务启动后需要向联系人确认课程时间",'
                '"progressSummary":"已发起课程时间确认",'
                '"completionReport":"","knownFacts":[],'
                '"pendingConditions":["等待联系人回复"],"evidenceEventIds":[]}'
            )
        if "ContextReviewAgent" in system_prompt:
            return (
                '{"decision":"APPROVE","reason":"候选回复有上下文依据","checks":{'
                '"contextCoherent":true,"personaAligned":true,"speakerConsistent":true,'
                '"worldKnowledgeConsistent":true,"naturalConversation":true,'
                '"answersLatestMessage":true,"currentStateGrounded":true},'
                '"unsupportedPersonalClaims":[],"entityConflicts":[]}'
            )
        if "审批" in system_prompt:
            return '{"decision":"APPROVE","reason":"候选回复有上下文依据"}'
        return "可以"


class OrchestratorWriteBackTest(unittest.IsolatedAsyncioTestCase):
    async def test_should_skip_delegated_event_when_persistent_claim_is_denied(self) -> None:
        """持久层拒绝事件租约时不得生成回复、发送 QQ 消息或写回任务状态。"""
        event_center_client = DummyEventCenterServiceClient()
        event_center_client.delegated_event_claim_allowed = False
        event_center_client.active_delegated_task = {
            "id": "delegated-task-leased",
            "status": "ACTIVE",
            "platform": "qq",
            "chatType": "private",
            "chatId": "3807050597",
            "targetName": "km",
            "objective": "确认晚上是否有空",
            "stateJson": "{}",
        }
        tools = ToolRegistry()
        send_tool = DummySendQqMessageTool()
        register_test_tool(tools, "send_qq_message", send_tool)
        register_delegated_runtime_tools(tools, event_center_client)
        service = OrchestratorService(
            router=RouterService(),
            planner=PlannerService(),
            tools=tools,
            memory=MemoryManager(),
            slow_channel_buffer=SlowChannelBuffer(window_seconds=600, max_messages=10),
            event_center_client=event_center_client,
            llm_client=DummyApprovedSocialLlm(),
            sleeper=DummySleeper(),
        )
        event = UnifiedEvent(
            eventId="qq:message:private:leased-event",
            platform="qq",
            scene="social",
            eventType="message",
            chatType="private",
            chatId="3807050597",
            sender=Sender(id="3807050597", name="km", role=None),
            text="晚上可以",
            attachments=[],
            mentions=[],
            timestamp="2026-07-28T20:00:00+08:00",
            rawPayload={"userId": "freeze", "messageOrigin": "EXTERNAL"},
        )

        result = await service.handle_event(event)

        self.assertEqual(["delegated_task_event:skipped"], result.write_back_actions)
        self.assertEqual("", result.final_reply)
        self.assertEqual(1, len(event_center_client.delegated_event_claims))
        self.assertEqual([], event_center_client.delegated_event_completions)
        self.assertEqual([], event_center_client.delegated_runtime_updates)
        self.assertEqual([], send_tool.calls)

    async def test_should_attach_workflow_facts_to_downstream_task_state(self) -> None:
        """下游步骤加载时必须注入父工作流已发布的事实，供上下文投影引用。

        回归：km 回复"九点"发布为 km_available_time，但 step_2 的模型上下文只有
        指令里的"明晚"，转告消息丢失了具体时间。这里验证任务加载后 stateJson
        携带 workflowFacts，后续 build_model_context 才能投影给模型。
        """

        class FactsClient(DummyEventCenterServiceClient):
            def __init__(self) -> None:
                super().__init__()
                self.active_delegated_task = {
                    "id": "step-2-task",
                    "status": "ACTIVE",
                    "workflowId": "wf-1",
                    "platform": "qq",
                    "chatType": "private",
                    "chatId": "2597164807",
                    "targetName": "小号",
                    "stateJson": '{"workingMemory": {"phase": "ACTIVE"}}',
                }

            async def get_delegated_workflow_runtime(self, user_id: str, workflow_id: str) -> dict:
                return {"id": workflow_id, "factsJson": '{"km_available_time": "九点"}'}

        client = FactsClient()
        service = OrchestratorService(
            router=RouterService(),
            planner=PlannerService(),
            tools=ToolRegistry(),
            memory=MemoryManager(),
            slow_channel_buffer=SlowChannelBuffer(window_seconds=600, max_messages=10),
            event_center_client=client,
        )
        event = UnifiedEvent(
            eventId="qq:private:2597164807:client:step-2:1",
            platform="qq",
            scene="delegated_task",
            eventType="delegated_workflow_step_activated",
            chatType="private",
            chatId="2597164807",
            sender=Sender(id="freeze", name="任务发起人", role="owner"),
            text="",
            attachments=[],
            mentions=[],
            timestamp="2026-08-21T16:30:00+08:00",
            rawPayload={"userId": "freeze"},
            actorType="SYSTEM",
            delegatedTaskId="step-2-task",
        )

        task = await service._get_active_delegated_task(event)

        self.assertIsNotNone(task)
        state = json.loads(task["stateJson"])
        self.assertEqual({"km_available_time": "九点"}, state["workflowFacts"])
        # 原始工作记忆不能被覆盖。
        self.assertEqual({"phase": "ACTIVE"}, state["workingMemory"])

    async def test_should_supersede_old_delegated_inbound_when_new_message_arrives(self) -> None:
        """同一会话连续来信时，旧消息在发送前必须让位给最新消息。"""
        service = OrchestratorService(
            router=RouterService(),
            planner=PlannerService(),
            tools=ToolRegistry(),
            memory=MemoryManager(),
            slow_channel_buffer=SlowChannelBuffer(window_seconds=600, max_messages=10),
            event_center_client=DummyEventCenterServiceClient(),
        )
        task = {"id": "delegated-task-burst"}
        old_event = UnifiedEvent(
            eventId="qq:message:private:burst-old",
            platform="qq",
            scene="social",
            eventType="message",
            chatType="private",
            chatId="3807050597",
            sender=Sender(id="3807050597", name="friend", role=None),
            text="第一条",
            attachments=[],
            mentions=[],
            timestamp="2026-07-24T10:00:00+08:00",
            rawPayload={"messageOrigin": "EXTERNAL"},
        )
        old_version = service._register_delegated_peer_inbound(old_event)
        service.sleeper = SupersedingSleeper(
            service,
            task["id"],
            "qq:message:private:burst-new",
        )

        should_continue = await service._wait_for_latest_delegated_inbound(old_event, old_version)

        self.assertFalse(should_continue)
        self.assertFalse(service._is_latest_delegated_inbound(old_event))
    async def test_should_send_once_when_parallel_delegated_tasks_share_same_conversation_turn(self) -> None:
        """同一会话被并行主控台任务处理时，相同候选回复只能实际写入 QQ 一次。"""
        tools = ToolRegistry()
        send_tool = DummySendQqMessageTool()
        register_test_tool(tools, "send_qq_message", send_tool)
        service = OrchestratorService(
            router=RouterService(),
            planner=PlannerService(),
            tools=tools,
            memory=MemoryManager(),
            slow_channel_buffer=SlowChannelBuffer(window_seconds=600, max_messages=10),
            event_center_client=DummyEventCenterServiceClient(),
            sleeper=DummySleeper(),
        )
        event = UnifiedEvent(
            eventId="delegated:start:parallel",
            platform="qq",
            scene="social",
            eventType="delegated_task_started",
            chatType="private",
            chatId="3807050597",
            sender=Sender(id="system", name="Memo Echo", role="system"),
            text="",
            attachments=[],
            mentions=[],
            timestamp="2026-07-27T20:00:00+08:00",
            direction="INTERNAL",
            actorType="SYSTEM",
            rawPayload={
                "userId": "freeze",
                "messageOrigin": "INTERNAL",
                "controlEvent": True,
            },
        )
        results = [
            AgentResult(
                task_id="parallel-task-a",
                agent="social_reply",
                status="success",
                reply_draft="晚上有空一起打三角洲吗",
            ),
            AgentResult(
                task_id="parallel-task-a",
                agent="review",
                status="success",
                structured_result={"reviewDecision": "APPROVE"},
            ),
        ]
        task_a = {
            "id": "parallel-task-a",
            "createdAt": "2026-07-27T20:00:00+08:00",
            "stateJson": "{}",
        }
        task_b = {
            "id": "parallel-task-b",
            "createdAt": "2026-07-27T20:00:01+08:00",
            "stateJson": "{}",
        }

        await asyncio.gather(
            service._write_back_if_needed(
                event,
                "social_reply",
                results,
                "晚上有空一起打三角洲吗",
                None,
                task_a,
            ),
            service._write_back_if_needed(
                event,
                "social_reply",
                results,
                "晚上有空一起打三角洲吗",
                None,
                task_b,
            ),
        )

        self.assertEqual(1, len(send_tool.calls))

    async def test_delegated_task_start_should_proactively_send_once(self) -> None:
        """主控台创建委托后应立即调用 QQ 发送工具，持久化开场状态后不得因重试重复发送。"""
        event_center_client = DummyEventCenterServiceClient()
        event_center_client.active_delegated_task = {
            "id": "delegated-task-kickoff",
            "status": "ACTIVE",
            "platform": "qq",
            "chatType": "private",
            "chatId": "3807050597",
            "targetName": "km",
            "originalCommand": "帮我和 km 预约明天晚上的课程",
            "objective": "预约明天晚上的家教课程",
            "successCriteria": "对方明确确认或拒绝课程时间",
            "deadlineText": "明天晚上",
            "progressSummary": "任务已创建，准备联系对方",
            "stateJson": "{}",
        }
        tools = ToolRegistry()
        send_tool = DummySendQqMessageTool()
        register_test_tool(tools, "send_qq_message", send_tool)
        register_delegated_runtime_tools(tools, event_center_client)
        service = OrchestratorService(
            router=RouterService(),
            planner=PlannerService(),
            tools=tools,
            memory=MemoryManager(),
            slow_channel_buffer=SlowChannelBuffer(window_seconds=600, max_messages=10),
            event_center_client=event_center_client,
            llm_client=DummyApprovedSocialLlm(),
            sleeper=DummySleeper(),
        )

        first_event = UnifiedEvent(
            eventId="delegated:start:kickoff-1",
            platform="qq",
            scene="social",
            eventType="delegated_task_started",
            chatType="private",
            chatId="3807050597",
            sender=Sender(id="system", name="Memo Echo", role="system"),
            text="",
            attachments=[],
            mentions=[],
            timestamp="2026-07-22T10:00:00+08:00",
            direction="INTERNAL",
            actorType="SYSTEM",
            rawPayload={"userId": "freeze", "messageOrigin": "INTERNAL", "controlEvent": True},
        )

        first_result = await service.handle_event(first_event)

        self.assertEqual("social_reply", first_result.route)
        self.assertEqual(1, len(send_tool.calls))
        self.assertTrue(any(action.startswith("qq_write_back_sent:") for action in first_result.write_back_actions))
        self.assertEqual(1, len(event_center_client.delegated_runtime_updates))
        self.assertEqual(1, len(event_center_client.delegated_event_claims))
        self.assertEqual(1, len(event_center_client.delegated_event_completions))
        self.assertEqual(
            event_center_client.delegated_event_claims[0]["eventId"],
            event_center_client.delegated_event_completions[0]["eventId"],
        )
        persisted_state = event_center_client.delegated_runtime_updates[0]["stateJson"]
        # 首发结果统一由运行时写回状态记录，避免维护独立的 kickoff 状态分支。
        self.assertIn('"lastWriteBackStatus": "SENT"', persisted_state)

        # 模拟 Java 已保存上轮状态后重新投递启动事件；动作图只能返回 WAIT。
        event_center_client.active_delegated_task["stateJson"] = persisted_state
        retry_event = first_event.model_copy(
            update={"event_id": "delegated:start:kickoff-2"}
        )
        retry_result = await service.handle_event(retry_event)

        self.assertEqual(1, len(send_tool.calls))
        self.assertIn("delegated_task_action:wait", retry_result.write_back_actions)
        self.assertEqual(2, len(event_center_client.delegated_event_claims))
        self.assertEqual(2, len(event_center_client.delegated_event_completions))

    async def test_should_restore_active_delegated_task_and_persist_runtime_progress(self) -> None:
        """活动委托必须覆盖普通路由，并兼容缺少 direction 的真实 NapCat 入站事件。"""
        event_center_client = DummyEventCenterServiceClient()
        event_center_client.active_delegated_task = {
            "id": "delegated-task-001",
            "status": "ACTIVE",
            "platform": "qq",
            "chatType": "private",
            "chatId": "3807050597",
            "targetName": "km",
            "originalCommand": "帮我问 km 明天下午是否有空",
            "objective": "确认 km 明天下午是否有空",
            "successCriteria": "对方明确接受、拒绝或提出其他时间",
            "deadlineText": "明天下午",
            "progressSummary": "已发起询问，等待对方回复",
            "stateJson": '{"knownFacts":[],"pendingConditions":["等待对方回复"]}',
        }
        tools = ToolRegistry()
        send_tool = DummySendQqMessageTool()
        register_test_tool(tools, "send_qq_message", send_tool)
        register_delegated_runtime_tools(tools, event_center_client)
        service = OrchestratorService(
            router=RouterService(),
            planner=PlannerService(),
            tools=tools,
            memory=MemoryManager(),
            slow_channel_buffer=SlowChannelBuffer(window_seconds=600, max_messages=10),
            event_center_client=event_center_client,
            llm_client=DummyApprovedSocialLlm(),
            sleeper=DummySleeper(),
        )
        event = UnifiedEvent(
            eventId="qq:message:private:delegated-001",
            platform="qq",
            scene="social",
            eventType="message",
            chatType="private",
            chatId="3807050597",
            sender=Sender(id="3807050597", name="km", role=None),
            text="下午行吗",
            attachments=[],
            mentions=[],
            timestamp="2026-07-22T14:00:00+08:00",
            # 线上事件目前只稳定保留 EXTERNAL 来源，direction 和 actorType 可能均缺失。
            rawPayload={"userId": "freeze", "messageOrigin": "EXTERNAL"},
        )

        result = await service.handle_event(event)

        self.assertEqual("social_reply", result.route)
        self.assertEqual("delegated-task-001", result.execution_id)
        self.assertEqual(1, len(send_tool.calls))
        self.assertEqual(1, len(event_center_client.delegated_runtime_updates))
        self.assertEqual("delegated-task-001", event_center_client.delegated_runtime_updates[0]["taskId"])
        self.assertTrue(
            any(action.startswith("delegated_task_runtime_updated:") for action in result.write_back_actions)
        )

    def test_should_collect_unique_verified_memory_ids_for_audit(self) -> None:
        """验证执行结果只暴露本轮注入的非空记忆 ID，并保持首次出现顺序。"""
        memories = [
            SimpleNamespace(id="memory-001"),
            SimpleNamespace(id=" memory-002 "),
            SimpleNamespace(id="memory-001"),
            SimpleNamespace(id=""),
        ]

        memory_ids = OrchestratorService._collect_verified_memory_ids(memories)

        self.assertEqual(["memory-001", "memory-002"], memory_ids)

    async def test_should_use_dedicated_vision_route_before_image_analysis(self) -> None:
        """验证图片链路优先使用视觉 route，且附件服务只拿到已解包的内部模型配置。"""
        media_service = CaptureMediaAnalysisService()
        service = OrchestratorService(
            router=RouterService(),
            planner=PlannerService(),
            tools=ToolRegistry(),
            memory=MemoryManager(),
            slow_channel_buffer=SlowChannelBuffer(window_seconds=600, max_messages=10),
            event_center_client=DummyVisionModelEventCenterClient(),
            media_analysis_service=media_service,
        )
        event = UnifiedEvent(
            eventId="qq:message:private:image-model-001",
            platform="qq",
            scene="social",
            eventType="message",
            chatType="private",
            chatId="10001",
            sender=Sender(id="10001", name="friend", role=None),
            text="[图片]",
            attachments=[{"fileId": "image-1", "fileName": "notice.png", "fileType": "image"}],
            mentions=[],
            timestamp="2026-07-13T10:00:00+08:00",
            rawPayload={},
        )

        result = await service.handle_event(event)

        self.assertEqual("social_reply", result.route)
        self.assertIsInstance(media_service.model_profile, ResolvedUserModelProfile)
        self.assertEqual("vision_analysis-model", media_service.model_profile.model)
        self.assertEqual("social_reply", service.event_center_client.model_calls[0]["route"])
        self.assertEqual("vision_analysis", service.event_center_client.model_calls[1]["route"])
        self.assertIsNone(service.event_center_client.model_calls[1]["profileId"])

    async def test_should_keep_conversation_qwen_when_default_model_does_not_declare_vision_route(self) -> None:
        """全局默认文本模型不能覆盖设定集显式选择的 Qwen3-VL，否则图片会被错误发给 DeepSeek。"""
        media_service = CaptureMediaAnalysisService()
        service = OrchestratorService(
            router=RouterService(),
            planner=PlannerService(),
            tools=ToolRegistry(),
            memory=MemoryManager(),
            slow_channel_buffer=SlowChannelBuffer(window_seconds=600, max_messages=10),
            event_center_client=DummyConversationVisionPreferredEventCenterClient(),
            media_analysis_service=media_service,
        )
        event = UnifiedEvent(
            eventId="qq:message:private:image-model-conversation",
            platform="qq",
            scene="social",
            eventType="message",
            chatType="private",
            chatId="10001",
            sender=Sender(id="10001", name="friend", role=None),
            text="[图片]",
            attachments=[{"fileId": "image-1", "fileName": "notice.png", "fileType": "image"}],
            mentions=[],
            timestamp="2026-07-13T16:35:00+08:00",
            rawPayload={},
        )

        await service.handle_event(event)

        self.assertIsNotNone(media_service.model_profile)
        self.assertEqual("qwen3-vl-flash", media_service.model_profile.model)
        self.assertNotEqual("deepseek-v4-pro", media_service.model_profile.model)

    async def test_should_fail_closed_when_profile_is_unavailable(self) -> None:
        # 这个测试函数的作用是验证设定查询失败时绝不自动发送，避免认证故障绕过 DRAFT_ONLY。
        self.assertFalse(OrchestratorService._should_auto_write_back(None))
        self.assertEqual(
            ["qq_write_back_skipped:profile_unavailable"],
            OrchestratorService._build_write_back_skip_actions(None),
        )

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

    async def test_workspace_command_should_create_parent_workflow_and_return_success(self) -> None:
        """验证主控台命令创建父工作流后始终返回前端可展示的最终回复。"""

        class WorkspaceCommandEventCenterClient(DummyEventCenterServiceClient):
            """为主控台命令补齐联系人读取和父工作流创建的最小测试实现。"""

            def __init__(self) -> None:
                super().__init__()
                self.created_execution_ids: list[str | None] = []

            async def list_delegated_task_candidates(self, user_id: str) -> list[ConversationCandidate]:
                return [
                    ConversationCandidate(
                        platform="qq",
                        chatType="private",
                        chatId="3807050597",
                        chatName="km",
                    )
                ]

            async def create_delegated_workflow(
                self,
                user_id: str,
                command: str,
                title: str,
                workflow_type: str,
                steps: list[dict],
                execution_id: str | None = None,
            ) -> dict:
                # 记录主控台执行批次，验证同一批次标识会跨 Python/Java 边界传递。
                self.created_execution_ids.append(execution_id)
                return {
                    "id": "workspace-wf-001",
                    "status": "RUNNING",
                    "stepCount": len(steps),
                }

        class WorkspaceCommandWorkflow:
            """固定路由和编译结果，使测试只关注 Orchestrator 的返回契约。"""

            async def resolve_workspace_command_targets(self, command, candidates, model_profile):
                # 明确联系人应由本地解析器命中，这里直接返回全部授权候选。
                return candidates

            async def plan_workspace_command(self, command, candidates, model_profile):
                # 这个函数的作用是模拟 RouterAgent 生成单步骤执行计划。
                target = candidates[0]
                return DelegatedWorkflowPlan(
                    title="和 km 确认明晚打游戏",
                    steps=[
                        DelegatedWorkflowPlanStep(
                            stepKey="step_1",
                            order=1,
                            instruction=command,
                            targetChatType=target.chat_type,
                            targetChatId=target.chat_id,
                        )
                    ],
                )

            async def compile_task(self, request, model_profile):
                return DelegatedTaskCompileResponse(
                    recognized=True,
                    platform="qq",
                    chatType="private",
                    chatId="3807050597",
                    targetName="km",
                    objective="和 km 确认明晚打游戏",
                    successCriteria="对方确认时间",
                    initialProgress="准备联系 km",
                )

        event_center_client = WorkspaceCommandEventCenterClient()
        service = OrchestratorService(
            router=RouterService(),
            planner=PlannerService(),
            tools=ToolRegistry(),
            memory=MemoryManager(),
            slow_channel_buffer=SlowChannelBuffer(window_seconds=600, max_messages=10),
            event_center_client=event_center_client,
        )
        service.delegated_task_workflow = WorkspaceCommandWorkflow()
        event = UnifiedEvent(
            eventId="desktop:command:workspace-create-task",
            platform="desktop",
            scene="workspace",
            eventType="desktop_command",
            chatType="private",
            chatId="workspace:freeze",
            sender=Sender(id="freeze", name="freeze", role="owner"),
            text="帮我约 km 明晚打游戏",
            attachments=[],
            mentions=[],
            timestamp="2026-07-28T12:00:00+08:00",
            rawPayload={"userId": "freeze", "executionId": "desktop-e2e-001"},
        )

        result = await service._handle_desktop_workspace_command(event)

        self.assertEqual(result.status, "success")
        self.assertEqual(result.final_reply, "委托任务已创建，正在按步骤执行")
        self.assertEqual(result.write_back_actions, ["delegated_workflow_created:workspace-wf-001"])
        # 委托步骤的首次执行统一由 Java outbox 激活，Python 不再维护本地异步启动旁路。
        self.assertFalse(hasattr(service, "_trigger_delegated_task_start"))
        self.assertEqual(event_center_client.created_execution_ids, ["desktop-e2e-001"])

    async def test_workspace_command_should_create_one_parent_workflow_for_multiple_contacts(self) -> None:
        """一条多联系人命令只创建一个父工作流，并为每个目标生成一个独立步骤。"""

        class MultiContactEventCenterClient(DummyEventCenterServiceClient):
            """提供两个私聊和一个同名群聊，并记录跨边界创建参数。"""

            def __init__(self) -> None:
                super().__init__()
                self.created_workflows: list[dict] = []

            async def list_delegated_task_candidates(self, user_id: str) -> list[ConversationCandidate]:
                return [
                    ConversationCandidate(
                        platform="qq",
                        chatType="private",
                        chatId="3807050597",
                        chatName="km",
                        aliases=["km", "刘畅"],
                    ),
                    ConversationCandidate(
                        platform="qq",
                        chatType="private",
                        chatId="2597164807",
                        chatName="小号",
                        aliases=["小号", "freeze"],
                    ),
                    ConversationCandidate(
                        platform="qq",
                        chatType="group",
                        chatId="777376261",
                        chatName="小号、km、哈吉仙",
                    ),
                ]

            async def create_delegated_workflow(
                self,
                user_id: str,
                command: str,
                title: str,
                workflow_type: str,
                steps: list[dict],
                execution_id: str | None = None,
            ) -> dict:
                self.created_workflows.append(
                    {
                        "id": "workflow-multi",
                        "status": "RUNNING",
                        "executionId": execution_id,
                        "steps": steps,
                    }
                )
                return self.created_workflows[-1]

        class MultiContactWorkflow:
            """复用真实目标解析器，并为每个目标生成独立执行步骤。"""

            def __init__(self) -> None:
                from app.workflows.delegated_task_graph import DelegatedTaskWorkflow

                # 明确联系人应由本地解析器命中；若意外调用模型，该测试会直接暴露属性错误。
                self.router = DelegatedTaskWorkflow(object())

            async def resolve_workspace_command_targets(self, command, candidates, model_profile):
                # 先复用真实联系人解析，只挑选命令明确提到的私聊目标。
                return await self.router.resolve_workspace_command_targets(command, candidates, model_profile)

            async def plan_workspace_command(self, command, candidates, model_profile):
                return DelegatedWorkflowPlan(
                    title="通知 km 和小号今晚有课",
                    steps=[
                        DelegatedWorkflowPlanStep(
                            stepKey=f"step_{index}",
                            order=index,
                            instruction=f"通知 {target.chat_name} 今晚有课",
                            targetChatType=target.chat_type,
                            targetChatId=target.chat_id,
                        )
                        for index, target in enumerate(candidates, start=1)
                    ],
                )

            async def compile_task(self, request, model_profile):
                target = request.conversations[0]
                return DelegatedTaskCompileResponse(
                    recognized=True,
                    platform=target.platform,
                    chatType=target.chat_type,
                    chatId=target.chat_id,
                    targetName=target.chat_name,
                    objective=f"通知 {target.chat_name} 今晚有课",
                    successCriteria="消息成功送达",
                    initialProgress=f"准备通知 {target.chat_name}",
                )

        event_center_client = MultiContactEventCenterClient()
        service = OrchestratorService(
            router=RouterService(),
            planner=PlannerService(),
            tools=ToolRegistry(),
            memory=MemoryManager(),
            slow_channel_buffer=SlowChannelBuffer(window_seconds=600, max_messages=10),
            event_center_client=event_center_client,
        )
        service.delegated_task_workflow = MultiContactWorkflow()
        event = UnifiedEvent(
            eventId="desktop:command:notify-course",
            platform="desktop",
            scene="workspace",
            eventType="desktop_command",
            chatType="private",
            chatId="workspace:freeze",
            sender=Sender(id="freeze", name="freeze", role="owner"),
            text="通知 km 和小号今晚有课",
            attachments=[],
            mentions=[],
            timestamp="2026-07-31T12:00:00+08:00",
            rawPayload={"userId": "freeze", "executionId": "desktop-notify-course-001"},
        )

        result = await service._handle_desktop_workspace_command(event)

        self.assertEqual(result.status, "success")
        # 一条命令只创建一个父工作流，不再按联系人复制任务。
        self.assertEqual(len(event_center_client.created_workflows), 1)
        self.assertEqual(result.write_back_actions, ["delegated_workflow_created:workflow-multi"])
        created_steps = event_center_client.created_workflows[0]["steps"]
        self.assertEqual(len(created_steps), 2)
        self.assertEqual(
            {str(step["compilation"]["chatId"]) for step in created_steps},
            {"3807050597", "2597164807"},
        )
        self.assertEqual(
            event_center_client.created_workflows[0]["executionId"],
            "desktop-notify-course-001",
        )
        # 多联系人任务同样只负责持久化，不能在 Python 进程内重复触发首次执行。
        self.assertFalse(hasattr(service, "_trigger_delegated_task_start"))

    async def test_should_intersect_profile_tools_with_skill_tool_policy(self) -> None:
        # 这个测试函数的作用是验证会话 profile 的工具白名单会和 skill 自带的工具白名单取交集，避免暴露越权工具。
        tools = ToolRegistry()
        register_test_tool(tools, "send_qq_message", DummySendQqMessageTool())
        register_test_tool(tools, "create_task", DummyCreateTaskTool())
        register_test_tool(tools, "list_tasks", DummyListTasksTool())
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

    async def test_privileged_group_tool_is_an_incremental_profile_grant(self) -> None:
        # 这个测试函数的作用是验证群管理授权只增加特权工具，不会误删默认普通工具。
        tools = ToolRegistry()
        register_test_tool(tools, "send_qq_message", DummySendQqMessageTool())
        register_test_tool(tools, "query_qq_group", DummyListTasksTool())
        register_test_tool(tools, "manage_qq_group", DummySendQqMessageTool())
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
            reason="命中群管理设定",
            profile=ConversationProfile(
                id="profile-group-001",
                name="群管理",
                chatType="group",
                allowedTools=["manage_qq_group"],
            ),
        )

        allowed_tools = service._resolve_allowed_tools(profile_match, [])

        # 工具注册表会按名称稳定排序；权限测试只关心授权集合，不把展示顺序当成安全语义。
        self.assertSetEqual(
            set(allowed_tools),
            {"send_qq_message", "query_qq_group", "manage_qq_group"},
        )

    async def test_conversation_profile_cannot_receive_delegated_task_state_tools(self) -> None:
        """验证设定集无法获得主控台委托的更新和自主结束工具。"""
        tools = ToolRegistry()
        register_test_tool(tools, "send_qq_message", DummySendQqMessageTool())
        register_test_tool(tools, "update_delegated_task", DummyListTasksTool())
        register_test_tool(tools, "complete_delegated_task", DummyListTasksTool())
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
            reason="命中长期私聊设定",
            profile=ConversationProfile(
                id="profile-private-001",
                name="长期私聊代理",
                allowedTools=[
                    "send_qq_message",
                    "update_delegated_task",
                    "complete_delegated_task",
                ],
            ),
        )

        allowed_tools = service._resolve_allowed_tools(profile_match, [])

        self.assertEqual(["send_qq_message"], allowed_tools)

        # 数据库中若只残留旧版委托工具名，也应按无效授权忽略，而不是让设定集
        # 获得结束能力或意外失去全部普通聊天工具。
        stale_profile_match = ConversationProfileMatchResult(
            matched=True,
            reason="旧版设定集残留了主控台工具",
            profile=ConversationProfile(
                id="profile-private-stale",
                name="旧版长期私聊代理",
                allowedTools=["update_delegated_task", "complete_delegated_task"],
            ),
        )

        stale_allowed_tools = service._resolve_allowed_tools(stale_profile_match, [])

        self.assertEqual(["send_qq_message"], stale_allowed_tools)

    async def test_should_not_echo_group_summary_when_slow_channel_flushes(self) -> None:
        # 这个测试函数的作用是验证慢通道摘要只进入工作台，不会回声发送到原群。
        tools = ToolRegistry()
        send_tool = DummySendQqMessageTool()
        register_test_tool(tools, "send_qq_message", send_tool)
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
        self.assertEqual(send_tool.calls, [])
        self.assertEqual(result.write_back_actions, [])
        self.assertIsNotNone(result.notification)
        self.assertEqual(result.notification.aggregation_status, "SUMMARY_READY")

    async def test_should_send_at_reply_back_to_group_when_message_mentions_self(self) -> None:
        # 这个测试函数的作用是验证被 @ 后生成的回复会以 at + 文本分段形式回写群聊。
        tools = ToolRegistry()
        send_tool = DummySendQqMessageTool()
        register_test_tool(tools, "send_qq_message", send_tool)
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
        self.assertTrue(
            any(action.startswith("qq_write_back_sent:ok:") for action in result.write_back_actions)
        )

    async def test_should_run_file_analysis_flow_and_write_back_daily_plan_to_private_chat(self) -> None:
        # 这个测试函数的作用是验证附件分析链路最终会把工作计划回写到私聊。
        tools = ToolRegistry()
        send_tool = DummySendQqMessageTool()
        extract_tool = DummyExtractFileTextTool()
        create_task_tool = DummyCreateTaskTool()
        register_test_tool(tools, "send_qq_message", send_tool)
        register_test_tool(tools, "extract_file_text", extract_tool)
        register_test_tool(tools, "create_task", create_task_tool)
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
        self.assertTrue(
            any(action.startswith("qq_write_back_sent:ok:") for action in result.write_back_actions)
        )

    async def test_should_query_existing_tasks_and_write_back_today_plan_to_private_chat(self) -> None:
        # 这个测试函数的作用是验证“我今天该做什么”会走任务查询链路并把结果回写到私聊。
        tools = ToolRegistry()
        send_tool = DummySendQqMessageTool()
        list_tool = DummyListTasksTool()
        register_test_tool(tools, "send_qq_message", send_tool)
        register_test_tool(tools, "list_tasks", list_tool)
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
        self.assertTrue(
            any(action.startswith("qq_write_back_sent:ok:") for action in result.write_back_actions)
        )

    async def test_should_not_auto_write_back_when_profile_requires_draft_only(self) -> None:
        # 这个测试函数的作用是验证命中“只生成草稿”的设定后，私聊也不会自动回写到 QQ。
        tools = ToolRegistry()
        send_tool = DummySendQqMessageTool()
        register_test_tool(tools, "send_qq_message", send_tool)
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

    async def test_should_create_multiple_private_write_back_payloads_for_social_bubbles(self) -> None:
        # 这个函数的作用是验证私聊社交回复的两条气泡会变成两次 QQ 发送，而群聊和非社交消息不会进入该分支。
        payload = {
            "chat_type": "private",
            "chat_id": "2597164807",
            "message": "先别急\n晚点说",
        }

        payloads = OrchestratorService._build_message_payloads(payload, ["先别急", "晚点说"])

        self.assertEqual(len(payloads), 2)
        self.assertEqual(payloads[0]["message"], "先别急")
        self.assertEqual(payloads[1]["message"], "晚点说")

    def test_should_prefer_review_message_parts_after_auto_rewrite(self) -> None:
        """审查纠偏后必须发送新分段，不能继续使用 SocialAgent 审查前的旧草稿。"""
        results = [
            AgentResult(
                task_id="task-1",
                agent="social",
                status="success",
                structured_result={"messageParts": ["旧草稿"]},
            ),
            AgentResult(
                task_id="task-1",
                agent="review",
                status="approved",
                structured_result={
                    "reviewDecision": "APPROVE",
                    "approvedDraft": "还卖\n一个月15",
                    "messageParts": ["还卖", "一个月15"],
                },
            ),
        ]

        parts = OrchestratorService._extract_social_message_parts(results, "还卖\n一个月15")

        self.assertEqual(parts, ["还卖", "一个月15"])

    async def test_should_delay_auto_reply_when_profile_configures_reply_window(self) -> None:
        # 这个测试函数的作用是验证命中延迟回复策略后，orchestrator 会先执行延迟再发送消息。
        tools = ToolRegistry()
        send_tool = DummySendQqMessageTool()
        sleeper = DummySleeper()
        register_test_tool(tools, "send_qq_message", send_tool)
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
            llm_client=DummyApprovedSocialLlm(),
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
        self.assertEqual(result.write_back_actions[0], "qq_write_back_delayed:2s")
        self.assertTrue(result.write_back_actions[1].startswith("qq_write_back_sent:ok:"))

    async def test_should_complete_delegated_task_only_after_closing_message_is_sent(self) -> None:
        """主控台任务选择“发送并结束”后，必须等 QQ 确认发送成功才提交 COMPLETED。"""
        event_center_client = DummyEventCenterServiceClient()
        tools = ToolRegistry()
        register_delegated_runtime_tools(tools, event_center_client)
        service = OrchestratorService(
            router=RouterService(),
            planner=PlannerService(),
            tools=tools,
            memory=MemoryManager(),
            slow_channel_buffer=SlowChannelBuffer(window_seconds=600, max_messages=10),
            event_center_client=event_center_client,
        )
        event = UnifiedEvent(
            eventId="qq:message:private:cross-day-confirm",
            platform="qq",
            scene="life",
            eventType="message",
            chatType="private",
            chatId="3807050597",
            selfId="3969785168",
            sender=Sender(id="3807050597", name="km", role=None),
            text="好的 那就这么定了",
            attachments=[],
            mentions=[],
            timestamp="2026-07-23T12:33:00+08:00",
            rawPayload={"self_id": 3969785168, "user_id": 3807050597},
        )
        decision = DelegatedTaskActionDecision(
            action="SEND_AND_COMPLETE",
            reason="联系人已明确确认安排",
            progressSummary="今晚七点到九点的课程已经确认",
            messageInstruction="回复今晚见",
            stateJson='{"resolvedTimeText":"2026-07-23晚上七点到九点"}',
            lastEventId=event.event_id,
            completionReport="已约定今晚七点到九点上课",
            evidence=["对方明确确认"],
            requestedTool="complete_delegated_task",
        )

        response = await service._update_delegated_task_runtime(
            event=event,
            task={"id": "delegated-cross-day"},
            delegated_action=decision,
            history_context=[],
            final_reply="今晚见",
            write_back_actions=["qq_write_back_sent:ok"],
            model_profile=None,
            results=[],
        )

        self.assertIsNotNone(response)
        self.assertEqual("COMPLETED", response["status"])
        self.assertEqual("COMPLETED", event_center_client.delegated_runtime_updates[-1]["status"])

    async def test_should_keep_delegated_task_active_when_closing_message_send_fails(self) -> None:
        """收尾消息发送失败时不得误结束任务，而应保存为 ACTIVE 等待下一轮重试。"""
        event_center_client = DummyEventCenterServiceClient()
        tools = ToolRegistry()
        register_delegated_runtime_tools(tools, event_center_client)
        service = OrchestratorService(
            router=RouterService(),
            planner=PlannerService(),
            tools=tools,
            memory=MemoryManager(),
            slow_channel_buffer=SlowChannelBuffer(window_seconds=600, max_messages=10),
            event_center_client=event_center_client,
        )

        class RuntimeRecorder:
            """提供稳定的运行图回落结果，避免测试依赖外部模型配置。"""

            def __init__(self) -> None:
                self.calls = 0

            async def evaluate_runtime(self, runtime_input, model_profile=None):
                """记录发送失败后的重新评估，并返回继续等待的 ACTIVE 决策。"""
                self.calls += 1
                return DelegatedTaskActionDecision(
                    action="WAIT",
                    reason="收尾消息尚未发送成功",
                    progressSummary="等待重试收尾消息",
                    stateJson=runtime_input.task.get("stateJson", "{}"),
                    lastEventId=runtime_input.event.get("eventId", ""),
                    requestedTool="update_delegated_task",
                )

        runtime_recorder = RuntimeRecorder()
        service.delegated_task_workflow = runtime_recorder
        event = UnifiedEvent(
            eventId="qq:message:private:cross-day-send-failed",
            platform="qq",
            scene="life",
            eventType="message",
            chatType="private",
            chatId="3807050597",
            selfId="3969785168",
            sender=Sender(id="3807050597", name="km", role=None),
            text="好的 那就这么定了",
            attachments=[],
            mentions=[],
            timestamp="2026-07-23T12:33:00+08:00",
            rawPayload={"self_id": 3969785168, "user_id": 3807050597},
        )
        decision = DelegatedTaskActionDecision(
            action="SEND_AND_COMPLETE",
            reason="联系人已明确确认安排",
            progressSummary="今晚七点到九点的课程已经确认",
            messageInstruction="回复今晚见",
            stateJson='{"resolvedTimeText":"2026-07-23晚上七点到九点"}',
            lastEventId=event.event_id,
            completionReport="已约定今晚七点到九点上课",
            evidence=["对方明确确认"],
            requestedTool="complete_delegated_task",
        )

        response = await service._update_delegated_task_runtime(
            event=event,
            task={"id": "delegated-cross-day"},
            delegated_action=decision,
            history_context=[],
            final_reply="今晚见",
            write_back_actions=["qq_write_back_failed:connector_timeout"],
            model_profile=None,
            results=[],
        )

        self.assertEqual(1, runtime_recorder.calls)
        self.assertIsNotNone(response)
        self.assertEqual("ACTIVE", response["status"])
        self.assertEqual("ACTIVE", event_center_client.delegated_runtime_updates[-1]["status"])

    async def test_should_complete_parent_workflow_step_with_declared_facts_only(self) -> None:
        """子任务完成时只向父工作流提交步骤声明的事实，不泄漏临时推理状态。"""
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
            eventId="qq:message:private:workflow-complete",
            platform="qq",
            scene="life",
            eventType="message",
            chatType="private",
            chatId="3807050597",
            sender=Sender(id="3807050597", name="km", role=None),
            text="今晚七点可以",
            attachments=[],
            mentions=[],
            timestamp="2026-08-08T18:30:00+08:00",
            rawPayload={"userId": "freeze", "messageOrigin": "EXTERNAL"},
        )
        decision = SimpleNamespace(
            action="COMPLETE_TASK",
            requested_tool="complete_delegated_task",
            progress_summary="已确认课程时间",
            completion_report="km 确认今晚七点可以上课",
            state_json=(
                '{"producedFacts":{"scheduled_time":"2026-08-08T19:00:00+08:00",'
                '"contact_name":"km","internal_note":"不要提交"}}'
            ),
            evidence=["对方回复今晚七点可以"],
        )
        task = {
            "id": "child-task-001",
            "workflowId": "workflow-001",
            "stepKey": "collect-course-time",
            "producesFacts": ["scheduled_time", "contact_name"],
        }

        response = await service._finalize_send(
            event=event,
            task=task,
            delegated_action=decision,
            claim_token="claim:child-task-001:complete",
        )

        self.assertEqual("done", response.get("route"))
        self.assertTrue(response.get("persisted"))
        self.assertEqual(1, len(event_center_client.delegated_workflow_completions))
        completion = event_center_client.delegated_workflow_completions[0]
        self.assertEqual(
            {
                "scheduled_time": "2026-08-08T19:00:00+08:00",
                "contact_name": "km",
            },
            completion["producedFacts"],
        )
        self.assertNotIn("internal_note", completion["producedFacts"])

    async def test_should_fill_single_declared_fact_from_latest_peer_reply(self) -> None:
        """模型漏填单一声明事实时，应使用本轮对方原话完成父步骤并解锁下游步骤。"""
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
            eventId="qq:message:private:870825868",
            platform="qq",
            scene="life",
            eventType="message",
            chatType="private",
            chatId="3807050597",
            selfId="3969785168",
            sender=Sender(id="3807050597", name="km", role=None),
            text="七点半吧",
            attachments=[],
            mentions=[],
            timestamp="2026-08-11T18:04:00+08:00",
            # 复刻真实 NapCat 事件：没有 direction、actorType 和 messageOrigin。
            rawPayload={"userId": "freeze"},
        )
        decision = SimpleNamespace(
            action="COMPLETE_TASK",
            requested_tool="complete_delegated_task",
            progress_summary="已获得 km 的回复",
            completion_report="km 回复七点半",
            state_json="{}",
            evidence=["km 回复：七点半吧"],
        )
        task = {
            "id": "child-task-single-fact",
            "workflowId": "workflow-single-fact",
            "stepKey": "collect-course-time",
            "producesFacts": ["今晚的上课时间"],
        }

        response = await service._finalize_send(
            event=event,
            task=task,
            delegated_action=decision,
            claim_token="claim:child-task-single-fact:complete",
        )

        self.assertEqual("done", response.get("route"))
        self.assertEqual(1, len(event_center_client.delegated_workflow_completions))
        self.assertEqual(
            {"今晚的上课时间": "七点半吧"},
            event_center_client.delegated_workflow_completions[0]["producedFacts"],
        )

    async def test_should_recognize_current_event_and_publish_artifact_when_history_fails(self) -> None:
        """历史接口失败时，当前事件仍必须被识别并完成父步骤：km 回复“七点半”→ 发布 CLASS_TIME → 解锁小号步骤。"""

        class FakeHistoryResponse:
            status_code = 502
            text = (
                '{"status":502,"message":"事件时间线解析失败。 chatId=3807050597, '
                'dbHitCount=3, filteredAfter=1"}'
            )

        class FakeHistoryError(Exception):
            response = FakeHistoryResponse()

        class HistoryFailingClient(DummyEventCenterServiceClient):
            def __init__(self) -> None:
                super().__init__()
                self.history_calls: list[dict] = []
                self.history_attempts = 0

            async def list_conversation_messages(self, chat_id: str, **kwargs) -> list[dict]:
                self.history_attempts += 1
                self.history_calls.append({"chat_id": chat_id, **kwargs})
                raise FakeHistoryError("历史查询失败")

        class StubWorkflow:
            """固定返回完成决策，并记录统一上下文是否携带了当前事件。"""

            def __init__(self) -> None:
                self.seen_envelope: dict | None = None

            async def decide_action(self, action_input, model_profile):
                self.seen_envelope = action_input.context_envelope
                return DelegatedTaskActionDecision(
                    action="COMPLETE_TASK",
                    reason="对方已明确回复上课时间",
                    progressSummary="已获得 km 的回复",
                    stateJson="{}",
                    lastEventId="",
                    completionReport="km 回复七点半",
                    requestedTool="complete_delegated_task",
                )

        event_center_client = HistoryFailingClient()
        service = OrchestratorService(
            router=RouterService(),
            planner=PlannerService(),
            tools=ToolRegistry(),
            memory=MemoryManager(),
            slow_channel_buffer=SlowChannelBuffer(window_seconds=600, max_messages=10),
            event_center_client=event_center_client,
        )
        stub_workflow = StubWorkflow()
        service.delegated_task_workflow = stub_workflow
        event = UnifiedEvent(
            eventId="qq:message:private:km-reply",
            platform="qq",
            scene="life",
            eventType="message",
            chatType="private",
            chatId="3807050597",
            selfId="3969785168",
            sender=Sender(id="3807050597", name="km", role=None),
            text="七点半",
            attachments=[],
            mentions=[],
            timestamp="2026-08-11T18:04:00+08:00",
            rawPayload={"userId": "freeze"},
        )
        task = {
            "id": "child-task-ask-km",
            "workflowId": "workflow-class-time",
            "stepKey": "ask_km",
            "producesFacts": ["class_time"],
            # 步骤固化的会话范围与起点水位，历史查询必须从这里取参。
            "conversationScopeJson": '{"platform":"qq","chatType":"private","chatId":"3807050597"}',
            "startedAt": "2026-08-11T18:00:00+08:00",
            "startEventId": "qq:message:private:start",
        }

        execution = await service._delegated_execution(
            event=event,
            task=task,
            model_profile=None,
            claim_token="claim:child-task-ask-km:km-reply",
        )

        # 1) 每次执行前都把当前入站事件写入 L0。
        self.assertIn("child-task-ask-km", event_center_client.current_events)
        self.assertEqual(
            event_center_client.current_events["child-task-ask-km"]["eventId"],
            "qq:message:private:km-reply",
        )
        # 2) 历史查询参数来自步骤 conversationScope 与 startedAt 水位。
        self.assertGreaterEqual(event_center_client.history_attempts, 1)
        scope_call = event_center_client.history_calls[0]
        self.assertEqual(scope_call["chat_id"], "3807050597")
        self.assertEqual(scope_call["platform"], "qq")
        self.assertEqual(scope_call["chat_type"], "private")
        self.assertIn("2026-08-11T18:00:00", str(scope_call["after"]))
        # 3) 历史失败时当前事件仍进入统一上下文。
        self.assertIsNotNone(stub_workflow.seen_envelope)
        timeline_texts = [
            " ".join(str(row.get("text") or "").split())
            for row in (stub_workflow.seen_envelope.get("taskTimeline") or [])
        ]
        self.assertIn("七点半", timeline_texts)
        # 4) 主链路图原子持久化转换并发布类型化产物，Java 据此解锁小号步骤。
        self.assertEqual(execution.get("route"), "done")
        self.assertTrue(execution.get("persisted"))
        self.assertEqual(1, len(event_center_client.delegated_workflow_completions))
        completion = event_center_client.delegated_workflow_completions[0]
        self.assertEqual(completion["producedFacts"], {"class_time": "七点半"})
        self.assertEqual(completion["artifacts"][0]["type"], "CLASS_TIME")
        self.assertEqual(completion["artifacts"][0]["name"], "class_time")
        self.assertEqual(completion["artifacts"][0]["value"], "七点半")
        self.assertTrue(completion["sourceEventId"])
        self.assertEqual(completion["stepKey"], "ask_km")

    async def test_should_not_treat_legacy_private_self_echo_as_peer_reply(self) -> None:
        """缺少方向字段时，自身发送的私聊回显不能推进委托工作流。"""
        event = UnifiedEvent(
            eventId="qq:message:private:self-echo",
            platform="qq",
            scene="life",
            eventType="message",
            chatType="private",
            chatId="3807050597",
            selfId="3969785168",
            sender=Sender(id="3969785168", name="哈吉仙", role=None),
            text="今晚几点上课？",
            attachments=[],
            mentions=[],
            timestamp="2026-08-11T18:03:00+08:00",
            rawPayload={"userId": "freeze"},
        )

        self.assertFalse(OrchestratorService._is_delegated_peer_inbound(event))

    async def test_should_keep_parent_workflow_waiting_when_declared_facts_are_incomplete(self) -> None:
        """父步骤要求的事实不完整时保持子任务可重试，不能误推进依赖步骤。"""
        event_center_client = DummyEventCenterServiceClient()
        tools = ToolRegistry()
        register_delegated_runtime_tools(tools, event_center_client)
        service = OrchestratorService(
            router=RouterService(),
            planner=PlannerService(),
            tools=tools,
            memory=MemoryManager(),
            slow_channel_buffer=SlowChannelBuffer(window_seconds=600, max_messages=10),
            event_center_client=event_center_client,
        )
        event = UnifiedEvent(
            eventId="qq:message:private:workflow-missing-fact",
            platform="qq",
            scene="life",
            eventType="message",
            chatType="private",
            chatId="3807050597",
            sender=Sender(id="3807050597", name="km", role=None),
            text="可以",
            attachments=[],
            mentions=[],
            timestamp="2026-08-08T18:31:00+08:00",
            rawPayload={"userId": "freeze", "messageOrigin": "EXTERNAL"},
        )
        decision = SimpleNamespace(
            action="COMPLETE_TASK",
            requested_tool="complete_delegated_task",
            progress_summary="尝试完成",
            completion_report="",
            state_json='{"producedFacts":{"contact_name":"km"}}',
            evidence=[],
        )
        task = {
            "id": "child-task-002",
            "workflowId": "workflow-001",
            "stepKey": "collect-course-time",
            "producesFacts": ["scheduled_time", "contact_name"],
        }

        response = await service._finalize_send(
            event=event,
            task=task,
            delegated_action=decision,
            claim_token="claim:child-task-002:wait",
        )

        self.assertEqual("wait", response.get("route"))
        self.assertEqual([], event_center_client.delegated_workflow_completions)
        pending_state = json.loads(event_center_client.delegated_runtime_updates[-1]["stateJson"])
        self.assertEqual(["scheduled_time"], pending_state["workflowCompletionPending"]["missingFacts"])

    async def test_should_surface_parent_workflow_callback_failure_for_message_retry(self) -> None:
        """父工作流回调失败时主链路图释放租约返回重试，不静默丢失完成事件。"""
        event_center_client = DummyEventCenterServiceClient()
        event_center_client.delegated_workflow_completion_error = RuntimeError("event center unavailable")
        service = OrchestratorService(
            router=RouterService(),
            planner=PlannerService(),
            tools=ToolRegistry(),
            memory=MemoryManager(),
            slow_channel_buffer=SlowChannelBuffer(window_seconds=600, max_messages=10),
            event_center_client=event_center_client,
        )
        event = UnifiedEvent(
            eventId="qq:message:private:workflow-callback-failed",
            platform="qq",
            scene="life",
            eventType="message",
            chatType="private",
            chatId="3807050597",
            sender=Sender(id="3807050597", name="km", role=None),
            text="今晚七点可以",
            attachments=[],
            mentions=[],
            timestamp="2026-08-08T18:32:00+08:00",
            rawPayload={"userId": "freeze", "messageOrigin": "EXTERNAL"},
        )
        decision = SimpleNamespace(
            action="COMPLETE_TASK",
            requested_tool="complete_delegated_task",
            progress_summary="已确认课程时间",
            completion_report="已确认",
            state_json='{"producedFacts":{"scheduled_time":"2026-08-08T19:00:00+08:00"}}',
            evidence=["对方明确确认"],
        )
        task = {
            "id": "child-task-003",
            "workflowId": "workflow-001",
            "stepKey": "collect-course-time",
            "producesFacts": ["scheduled_time"],
        }

        response = await service._finalize_send(
            event=event,
            task=task,
            delegated_action=decision,
            claim_token="claim:child-task-003:retry",
        )

        # 回调失败：图返回可重试状态并释放租约，投递方据此重新投递。
        self.assertEqual("retry", response.get("route"))
        self.assertFalse(response.get("persisted"))
        self.assertEqual(0, len(event_center_client.delegated_workflow_completions))
        self.assertEqual(1, len(event_center_client.delegated_event_releases))


if __name__ == "__main__":
    unittest.main()
