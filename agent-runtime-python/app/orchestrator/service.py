from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import unicodedata
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.clients.connector_service import ConnectorServiceClient
from app.clients.event_center_service import EventCenterServiceClient
from app.clients.llm_service import LlmServiceClient
from app.clients.schedule_service import ScheduleServiceClient
from app.clients.task_service import TaskServiceClient
from app.memory.manager import MemoryManager
from app.memory.candidate_extractor import MemoryCandidateExtractor
from app.orchestrator.registry import build_agent_registry
from app.planner.service import PlannerService
from app.router.service import RouterService
from app.schemas.events import Sender, UnifiedEvent
from app.schemas.delegated_tasks import (
    ConversationCandidate,
    DelegatedTaskActionDecision,
    DelegatedTaskActionInput,
    DelegatedTaskCompileRequest,
    DelegatedTaskRuntimeInput,
)
from app.schemas.delegated_workflows import (
    DelegatedWorkflowStepExecutionRequest,
    DelegatedWorkflowStepExecutionResponse,
)
from app.schemas.model_profiles import UserModelProfileResolveResult
from app.schemas.profiles import ConversationProfileMatchResult
from app.schemas.results import AgentResult, NotificationDecision, OrchestratorResult, ToolCallRecord
from app.schemas.schedules import SemanticIntentDecision
from app.schemas.skills import SkillDescriptor
from app.schemas.tasks import AgentTaskContext
from app.services.slow_channel_buffer import SlowChannelBuffer
from app.services.conversation_state_service import ConversationStateService
from app.services.delegated_task_context import DelegatedTaskContextAssembler
from app.services.conversation_task_completion import ConversationTaskCompletionService
from app.services.media_analysis_service import MediaAnalysisService
from app.services.message_identity import canonical_message_identity, is_runtime_generated_message
from app.services.schedule_intent_classifier import SemanticScheduleIntentClassifier
from app.skills.resolver import SkillResolver
from app.tools.extract_file_text_tool import ExtractFileTextTool
from app.tools.langchain_runtime_tools import build_runtime_tools, runtime_tool_specs
from app.tools.registry import ToolRegistry
from app.tools.send_secure_asset_tool import SendSecureAssetTool
from app.tools.base import ToolExecutionContext
from app.tools.qq_group_operations_tool import ManageQqGroupTool, QueryQqGroupTool
from app.workflows.delegated_task_graph import DelegatedTaskWorkflow, WorkflowPlanningError


logger = logging.getLogger(__name__)


class DelegatedWorkflowCompletionError(RuntimeError):
    """父工作流步骤完成回调失败；调用方必须保留当前消息以便重试。"""


class DelegatedWorkflowFactsMissingError(DelegatedWorkflowCompletionError):
    """父步骤声明事实不足；这是可恢复的业务等待状态，不是基础设施故障。"""

    def __init__(self, missing_facts: list[str]) -> None:
        self.missing_facts = tuple(missing_facts)
        super().__init__("父工作流步骤缺少声明事实: " + ", ".join(missing_facts))


class OrchestratorService:
    def __init__(
        self,
        router: RouterService,
        planner: PlannerService,
        tools: ToolRegistry,
        memory: MemoryManager,
        slow_channel_buffer: SlowChannelBuffer,
        event_center_client: EventCenterServiceClient | None = None,
        llm_client: LlmServiceClient | None = None,
        skill_resolver: SkillResolver | None = None,
        media_analysis_service: MediaAnalysisService | None = None,
        sleeper=None,
        schedule_intent_classifier: SemanticScheduleIntentClassifier | None = None,
        conversation_state_service: ConversationStateService | None = None,
        memory_candidate_extractor: MemoryCandidateExtractor | None = None,
        task_completion_service: ConversationTaskCompletionService | None = None,
        delegated_task_workflow: DelegatedTaskWorkflow | None = None,
    ) -> None:
        # 这个构造函数的作用是保存运行时依赖，并一次性构建 agent 注册表。
        self.router = router
        self.planner = planner
        self.tools = tools
        self.memory = memory
        self.agents = build_agent_registry(tools, slow_channel_buffer, llm_client=llm_client)
        self.event_center_client = event_center_client
        self.llm_client = llm_client
        self.skill_resolver = skill_resolver or SkillResolver.build_default()
        self.media_analysis_service = media_analysis_service
        self.sleeper = sleeper or asyncio.sleep
        self.schedule_intent_classifier = schedule_intent_classifier
        self.conversation_state_service = conversation_state_service or ConversationStateService()
        self.memory_candidate_extractor = memory_candidate_extractor
        self.task_completion_service = task_completion_service
        # 委托图复用同一个模型客户端，确保命令编译和运行态审查遵守用户选择的模型配置。
        self.delegated_task_workflow = delegated_task_workflow or DelegatedTaskWorkflow(
            llm_client or LlmServiceClient(),
            self.event_center_client,
        )
        # 同一委托会话可能在极短时间内连续收到多条消息。用递增版本号做轻量合并，
        # 让旧事件主动让位给最新事件，避免分别对两条补充消息生成两次回复。
        self._delegated_conversation_versions: dict[str, int] = {}
        # 防抖窗口结束后，模型生成和平台回写仍可能继续数秒。保留最近事件 ID，
        # 让旧事件在真正发送前再做一次失效检查，而不是仅依赖 450ms 防抖。
        self._delegated_conversation_latest_event_ids: dict[str, str] = {}
        self._delegated_inbound_debounce_seconds = 0.45

    @classmethod
    def build_default(cls) -> "OrchestratorService":
        # 这个函数的作用是组装本地默认运行时依赖，方便直接启动整条链路。
        event_center_client = EventCenterServiceClient()
        llm_client = LlmServiceClient()
        tools = ToolRegistry()
        file_text_extractor = ExtractFileTextTool()
        schedule_service_client = ScheduleServiceClient()
        task_service_client = TaskServiceClient()
        connector_client = ConnectorServiceClient()
        secure_asset_sender = SendSecureAssetTool(event_center_client, connector_client)
        group_query_manager = QueryQqGroupTool(connector_client)
        group_operation_manager = ManageQqGroupTool(connector_client)
        tool_specs = runtime_tool_specs()
        for runtime_tool in build_runtime_tools(
            event_center_client=event_center_client,
            schedule_service_client=schedule_service_client,
            task_service_client=task_service_client,
            connector_client=connector_client,
            file_text_extractor=file_text_extractor,
            secure_asset_sender=secure_asset_sender,
            group_query_manager=group_query_manager,
            group_operation_manager=group_operation_manager,
        ):
            tools.register(runtime_tool, tool_specs[runtime_tool.name])
        # 群管理的批准、拒绝属于 Runtime 内部回调，不能作为 Agent 直接调用的工具暴露。
        tools.register_internal_service("manage_qq_group", group_operation_manager)
        slow_channel_buffer = SlowChannelBuffer()
        service = cls(
            router=RouterService(),
            planner=PlannerService(),
            tools=tools,
            memory=MemoryManager(event_center_client),
            slow_channel_buffer=slow_channel_buffer,
            event_center_client=event_center_client,
            llm_client=llm_client,
            skill_resolver=SkillResolver.build_default(),
            media_analysis_service=MediaAnalysisService(
                event_center_client,
                file_text_extractor,
                llm_client,
            ),
            schedule_intent_classifier=SemanticScheduleIntentClassifier.build_default(),
            memory_candidate_extractor=MemoryCandidateExtractor(event_center_client, llm_client),
            task_completion_service=ConversationTaskCompletionService(event_center_client, llm_client),
            delegated_task_workflow=DelegatedTaskWorkflow(llm_client, event_center_client),
        )
        slow_channel_buffer.set_flush_callback(service._publish_slow_channel_digest)
        return service

    async def analyze_attachments_in_background(self, event: UnifiedEvent) -> None:
        """异步执行媒体解析并回写，不参与当前 Webhook 请求的响应时间。"""
        if self.media_analysis_service is None or not event.attachments:
            return
        await self.media_analysis_service.analyze_event(event)

    async def _classify_schedule_intent(
        self,
        event: UnifiedEvent,
        current_route: str,
    ) -> SemanticIntentDecision:
        # 这个函数的作用是在关键词路由之前补充可选语义判断，只允许高置信 CREATE 意图覆盖普通消息路由。
        classifier = self.schedule_intent_classifier
        if current_route in {"schedule_extract", "task_plan", "file_analysis", "group_ops"}:
            return SemanticIntentDecision()
        if classifier is None or not classifier.is_enabled() or not (event.text or "").strip():
            return SemanticIntentDecision()
        try:
            return await classifier.classify(event.text or "")
        except Exception as exception:
            logger.info("日程语义门控不可用，继续使用原路由。eventId=%s, error=%s", event.event_id, exception)
            return SemanticIntentDecision()

    @staticmethod
    def _profile_has_preferred_route(profile_match: ConversationProfileMatchResult | None) -> bool:
        # 这个函数的作用是保护用户显式设置的 preferredRoute，防止语义门控越过会话级路由配置。
        return bool(
            profile_match
            and profile_match.active
            and profile_match.profile
            and profile_match.profile.preferred_route.strip()
        )

    async def _analyze_attachments_for_social_reply(
        self,
        event: UnifiedEvent,
        model_profile,
    ) -> list[dict[str, str]]:
        """在私聊自动回复前完成附件理解，让图片或文件内容能作为当前回复和审查层的证据。"""
        if self.media_analysis_service is None or not event.attachments:
            return []
        try:
            return await self.media_analysis_service.analyze_event(event, model_profile)
        except Exception as exception:
            logger.warning("当前私聊附件解析失败，继续按无附件上下文处理：eventId=%s, error=%s", event.event_id, exception)
            return []

    async def _publish_slow_channel_digest(self, flush) -> None:
        """把后台慢通道定时器产出的摘要交给事件中心持久化，失败时仅记录为后台任务失败。"""
        if self.event_center_client is None:
            return
        try:
            inbox_agent = self.agents["inbox"]
            structured_digest = await inbox_agent.summarize_slow_channel_batch(flush)
            flush.summary = structured_digest["summary"]
            flush.happened = structured_digest["happened"]
            flush.action_items = structured_digest["actionItems"]
            flush.next_step = structured_digest["nextStep"]
            await self.event_center_client.publish_slow_channel_digest(flush)
        except Exception:
            # 模型失败时仍尝试写入规则摘要，保证已经清空的缓冲批次不会丢失。
            try:
                await self.event_center_client.publish_slow_channel_digest(flush)
            except Exception:
                return

    @staticmethod
    def _is_desktop_workspace_command(event: UnifiedEvent) -> bool:
        """判断事件是否来自主控台自然语言输入框，主控台命令不能当作普通聊天消息处理。"""
        return event.platform == "desktop" and event.event_type == "desktop_command"

    @staticmethod
    def _log_delegated_trace(
        stage: str,
        *,
        execution_id: str,
        event: UnifiedEvent | None = None,
        task_id: str | None = None,
        **fields: Any,
    ) -> None:
        """记录主控台委托闭环的结构化阶段日志，避免输出聊天正文、密钥等敏感内容。"""
        resolved_task_id = task_id or ""
        if not resolved_task_id and event is not None:
            payload = event.raw_payload or {}
            resolved_task_id = str(payload.get("delegatedTaskId") or event.delegated_task_id or "")
        safe_fields = {key: value for key, value in fields.items() if value is not None}
        logger.info(
            "主控台委托闭环 | executionId=%s | taskId=%s | eventId=%s | stage=%s | data=%s",
            execution_id,
            resolved_task_id or "-",
            event.event_id if event is not None else "-",
            stage,
            json.dumps(safe_fields, ensure_ascii=False, default=str, separators=(",", ":")),
        )

    async def _handle_desktop_workspace_command(self, event: UnifiedEvent) -> OrchestratorResult:
        """处理主控台命令：读取联系人白名单、交给 LangGraph 编译任务，并在目标明确时主动启动。"""
        raw_payload = event.raw_payload or {}
        execution_id = str(
            raw_payload.get("executionId")
            or raw_payload.get("commandId")
            or f"desktop-command:{uuid4()}"
        )
        user_id = EventCenterServiceClient.resolve_event_user_id(event)
        command = (event.text or "").strip()
        self._log_delegated_trace(
            "command_received",
            execution_id=execution_id,
            event=event,
            userId=user_id,
            commandLength=len(command),
            requestedRoute=str(raw_payload.get("requestedRoute") or "auto"),
        )
        if not command:
            return OrchestratorResult(
                execution_id=execution_id,
                status="failed",
                route="delegated_task",
                summary="主控台命令为空",
                results=[],
                final_reply="请告诉我希望我处理什么事情",
            )
        if self.event_center_client is None:
            return OrchestratorResult(
                execution_id=execution_id,
                status="failed",
                route="delegated_task",
                summary="Event Center 未配置，无法创建委托任务",
                results=[],
                final_reply="暂时无法创建委托任务，请稍后重试",
            )

        try:
            candidates = await self.event_center_client.list_delegated_task_candidates(user_id)
        except Exception as exception:
            self._log_delegated_trace(
                "candidate_load_failed",
                execution_id=execution_id,
                event=event,
                userId=user_id,
                errorType=type(exception).__name__,
            )
            return OrchestratorResult(
                execution_id=execution_id,
                status="failed",
                route="delegated_task",
                summary="读取联系人失败，无法创建委托任务",
                results=[
                    AgentResult(
                        task_id=execution_id,
                        agent="delegated_task_router",
                        status="failed",
                        reply_draft=str(exception),
                        need_confirmation=True,
                    )
                ],
                final_reply="读取联系人失败，暂时无法创建委托任务",
            )

        if not candidates:
            self._log_delegated_trace(
                "candidate_load_completed",
                execution_id=execution_id,
                event=event,
                userId=user_id,
                candidateCount=0,
            )
            return OrchestratorResult(
                execution_id=execution_id,
                status="failed",
                route="delegated_task",
                summary="没有可用联系人候选，请先完成 QQ 连接和联系人同步",
                results=[],
                final_reply="没有可用联系人，请先完成 QQ 连接和联系人同步",
            )

        model_result = await self._safe_resolve_workspace_command_model(user_id)
        model_profile = model_result.profile if model_result else None
        # 主控台命令的目标解析统一交给 Python Runtime 的路由器。
        # Java 侧只负责提供授权候选，避免本地正则把“km预约”等动作词误拼进联系人名称。
        self._log_delegated_trace(
            "candidate_load_completed",
            execution_id=execution_id,
            event=event,
            userId=user_id,
            candidateCount=len(candidates),
            modelConfigured=bool(model_profile),
        )
        try:
            target_candidates = await self.delegated_task_workflow.resolve_workspace_command_targets(
                command=command,
                candidates=candidates,
                model_profile=model_profile,
            )
        except Exception as exception:
            self._log_delegated_trace(
                "target_resolution_failed",
                execution_id=execution_id,
                event=event,
                userId=user_id,
                errorType=type(exception).__name__,
            )
            return OrchestratorResult(
                execution_id=execution_id,
                status="failed",
                route="delegated_task",
                summary="委托目标解析失败",
                results=[],
                final_reply="暂时无法确认需要联系的对象，请稍后重试或补充联系人信息",
            )
        if not target_candidates:
            self._log_delegated_trace(
                "targets_unresolved",
                execution_id=execution_id,
                event=event,
                candidateCount=len(candidates),
            )
            return OrchestratorResult(
                execution_id=execution_id,
                status="failed",
                route="delegated_task",
                summary="未能确认委托目标",
                results=[
                    AgentResult(
                        task_id=f"{execution_id}:targets",
                        agent="delegated_task_router",
                        status="needs_clarification",
                        reply_draft="请补充需要联系的好友或群聊",
                        need_confirmation=True,
                    )
                ],
                final_reply="请补充需要联系的好友或群聊",
            )

        self._log_delegated_trace(
            "targets_resolved",
            execution_id=execution_id,
            event=event,
            candidateCount=len(candidates),
            targetCount=len(target_candidates),
            targets=[
                {
                    "platform": candidate.platform,
                    "chatType": candidate.chat_type,
                    "chatId": candidate.chat_id,
                    "chatName": candidate.chat_name,
                }
                for candidate in target_candidates
            ],
        )
        try:
            # RouterAgent 先生成父工作流。依赖关系在这里一次性确定，后续不得再按联系人拆成独立任务。
            plan = await self.delegated_task_workflow.plan_workspace_command(
                command=command,
                candidates=target_candidates,
                model_profile=model_profile,
            )
            candidate_map = {
                (self._normalize_workspace_chat_type(candidate.chat_type), candidate.chat_id): candidate
                for candidate in target_candidates
            }
            compiled_steps: list[dict[str, Any]] = []
            for step in sorted(plan.steps, key=lambda item: item.order):
                candidate = candidate_map.get((step.target_chat_type, step.target_chat_id))
                if candidate is None:
                    raise WorkflowPlanningError(f"步骤 {step.step_key} 引用了未授权会话")

                compilation = await self.delegated_task_workflow.compile_task(
                    DelegatedTaskCompileRequest(
                        userId=user_id,
                        command=step.instruction,
                        conversations=[candidate],
                        targetResolvedByRouter=True,
                    ),
                    model_profile,
                )
                if not compilation.recognized or bool(getattr(compilation, "needs_clarification", False)):
                    question = compilation.clarification_question or f"步骤 {step.step_key} 缺少执行信息"
                    raise WorkflowPlanningError(question)

                compiled_steps.append(
                    {
                        "stepKey": step.step_key,
                        "order": step.order,
                        "role": step.role,
                        "instruction": step.instruction,
                        "dependsOn": step.depends_on,
                        "requiredFacts": step.required_facts,
                        "producesFacts": step.produces_facts,
                        "compilation": compilation.model_dump(by_alias=True),
                    }
                )
                self._log_delegated_trace(
                    "workflow_step_compiled",
                    execution_id=execution_id,
                    event=event,
                    stepKey=step.step_key,
                    dependsOn=step.depends_on,
                    targetChatId=step.target_chat_id,
                )

            workflow = await self.event_center_client.create_delegated_workflow(
                user_id=user_id,
                command=command,
                title=plan.title,
                workflow_type=plan.workflow_type,
                steps=compiled_steps,
                execution_id=execution_id,
            )
            workflow_id = str(workflow.get("id") or workflow.get("workflowId") or execution_id)
            workflow_status = str(workflow.get("status") or "RUNNING")
            self._log_delegated_trace(
                "workflow_created",
                execution_id=execution_id,
                event=event,
                workflowId=workflow_id,
                workflowStatus=workflow_status,
                stepCount=len(compiled_steps),
            )
            return OrchestratorResult(
                execution_id=execution_id,
                status="success",
                route="delegated_task",
                summary=f"已创建包含 {len(compiled_steps)} 个步骤的委托工作流",
                results=[
                    AgentResult(
                        task_id=workflow_id,
                        agent="delegated_task_router",
                        status=workflow_status.lower(),
                        structured_result={"workflow": workflow, "plan": plan.model_dump(by_alias=True)},
                        reply_draft=str(workflow.get("initialProgress") or "委托工作流已创建，正在执行首个步骤"),
                        tool_calls=[
                            ToolCallRecord(
                                tool="create_delegated_workflow",
                                arguments={"workflowId": workflow_id, "stepCount": len(compiled_steps)},
                            )
                        ],
                        next_actions=["系统只激活无依赖的根步骤，后继步骤将在所需事实就绪后自动执行"],
                    )
                ],
                final_reply="委托任务已创建，正在按步骤执行",
                write_back_actions=[f"delegated_workflow_created:{workflow_id}"],
            )
        except WorkflowPlanningError as exception:
            self._log_delegated_trace(
                "workflow_planning_failed",
                execution_id=execution_id,
                event=event,
                error=str(exception),
            )
            return OrchestratorResult(
                execution_id=execution_id,
                status="failed",
                route="delegated_task",
                summary="委托工作流缺少可执行信息",
                results=[
                    AgentResult(
                        task_id=f"{execution_id}:plan",
                        agent="delegated_task_router",
                        status="needs_clarification",
                        reply_draft=str(exception),
                        need_confirmation=True,
                    )
                ],
                final_reply=str(exception),
            )
        except Exception as exception:
            logger.exception("主控台委托工作流创建失败：executionId=%s", execution_id)
            return OrchestratorResult(
                execution_id=execution_id,
                status="failed",
                route="delegated_task",
                summary="委托工作流创建失败",
                results=[
                    AgentResult(
                        task_id=f"{execution_id}:failed",
                        agent="delegated_task_router",
                        status="failed",
                        reply_draft=str(exception),
                        need_confirmation=True,
                    )
                ],
                final_reply="委托工作流创建失败，请稍后重试",
            )

    async def _safe_resolve_workspace_command_model(
        self,
        user_id: str,
    ) -> UserModelProfileResolveResult | None:
        """读取主控台命令编译模型；读取失败时返回 None，让 LangGraph 走自身降级逻辑。"""
        if self.event_center_client is None:
            return None
        try:
            return await self.event_center_client.resolve_user_model_profile("message_dispatch", user_id=user_id)
        except Exception as exception:
            logger.info("主控台命令模型配置读取失败，使用 Runtime 默认模型：userId=%s, error=%s", user_id, exception)
            return None

    @staticmethod
    def _normalize_workspace_chat_type(chat_type: str | None) -> str:
        """统一 Planner 与联系人白名单中的会话类型，避免 channel/room 导致目标校验误判。"""
        normalized = str(chat_type or "").strip().lower()
        return "group" if normalized in {"group", "channel", "room"} else "private"

    async def execute_delegated_workflow_step(
        self,
        request: DelegatedWorkflowStepExecutionRequest,
    ) -> DelegatedWorkflowStepExecutionResponse:
        """校验 outbox 指向的激活版本，只执行当前仍然有效的工作流步骤。"""
        if self.event_center_client is None:
            raise RuntimeError("event-center client is required")

        workflow = await self.event_center_client.get_delegated_workflow_runtime(
            request.user_id,
            request.workflow_id,
        )
        if str(workflow.get("status") or "").upper() != "RUNNING":
            return self._ignored_workflow_step_execution(request, "workflow_not_running")

        steps = workflow.get("steps")
        step = next(
            (
                item
                for item in (steps if isinstance(steps, list) else [])
                if isinstance(item, dict)
                and str(item.get("stepKey") or "").strip() == request.step_key
            ),
            None,
        )
        if step is None:
            return self._ignored_workflow_step_execution(request, "step_not_found")
        if str(step.get("taskId") or "").strip() != request.task_id:
            return self._ignored_workflow_step_execution(request, "task_mismatch")
        if str(step.get("status") or "").upper() != "ACTIVE":
            return self._ignored_workflow_step_execution(request, "step_not_active")
        try:
            activation_version = int(step.get("activationVersion") or 0)
        except (TypeError, ValueError):
            activation_version = 0
        if activation_version != request.activation_version:
            return self._ignored_workflow_step_execution(request, "stale_activation_version")

        chat_id = str(step.get("chatId") or "").strip()
        if not chat_id:
            return self._ignored_workflow_step_execution(request, "chat_not_resolved")

        now = datetime.now(timezone.utc).isoformat()
        start_event = UnifiedEvent(
            eventId=request.idempotency_key,
            platform=str(step.get("platform") or "qq"),
            scene="delegated_task",
            eventType="delegated_workflow_step_activated",
            chatType=str(step.get("chatType") or "private"),
            chatId=chat_id,
            selfId="",
            sender=Sender(id=request.user_id, name="任务发起人", role="owner"),
            text="",
            attachments=[],
            mentions=[],
            segments=[],
            timestamp=now,
            rawPayload={
                "source": "delegated-workflow-outbox",
                "userId": request.user_id,
                "requestedRoute": "social_reply",
                "delegatedTaskId": request.task_id,
                "delegatedWorkflowId": request.workflow_id,
                "delegatedWorkflowStepKey": request.step_key,
                "activationVersion": request.activation_version,
                "executionId": request.idempotency_key,
                "controlEvent": True,
            },
            actorType="SYSTEM",
            platformMessageId="",
            clientMessageId=request.idempotency_key,
            correlationId=request.workflow_id,
            sequence=None,
            sentAt=now,
            receivedAt=now,
            importedAt=None,
            direction="INTERNAL",
            delegatedTaskId=request.task_id,
        )
        self._log_delegated_trace(
            "workflow_step_started",
            execution_id=request.idempotency_key,
            event=start_event,
            workflowId=request.workflow_id,
            stepKey=request.step_key,
            activationVersion=request.activation_version,
        )
        # outbox 步骤必须由最外层持有事件租约。handle_event 内部可能因为暂时缺少模型、
        # 联系人或工具而正常返回“无动作”，此时不能提前把事件标记为已完成。
        delegated_task_ref = {"id": request.task_id}
        claim_token = await self._claim_delegated_event(start_event, delegated_task_ref)
        if claim_token == "":
            return DelegatedWorkflowStepExecutionResponse(
                status="deferred",
                reason="event_claim_unavailable",
                workflowId=request.workflow_id,
                stepKey=request.step_key,
                retryable=True,
                writeBackActions=[],
            )
        if claim_token:
            start_event.raw_payload["_delegatedEventClaimToken"] = claim_token
            start_event.raw_payload["_deferDelegatedClaimCompletion"] = True
        try:
            result = await self.handle_event(start_event)
        except Exception:
            await self._release_delegated_event(
                start_event,
                delegated_task_ref,
                claim_token,
            )
            raise
        write_back_actions = list(getattr(result, "write_back_actions", None) or [])
        has_persistent_effect = any(
            action
            and action not in {
                "delegated_task_event:skipped",
                "delegated_task_action:superseded",
            }
            for action in write_back_actions
        )
        self._log_delegated_trace(
            "workflow_step_finished",
            execution_id=request.idempotency_key,
            event=start_event,
            workflowId=request.workflow_id,
            stepKey=request.step_key,
            activationVersion=request.activation_version,
            persistentEffect=has_persistent_effect,
            writeBackActions=write_back_actions,
        )
        if not has_persistent_effect:
            # handle_event 会把认领冲突、暂时无动作等情况作为正常结果返回。
            # 再读一次服务端状态，只有同一激活版本仍未推进时才要求 outbox 重试。
            refreshed_workflow = await self.event_center_client.get_delegated_workflow_runtime(
                request.user_id,
                request.workflow_id,
            )
            if self._is_same_active_workflow_step(refreshed_workflow, request):
                await self._release_delegated_event(
                    start_event,
                    delegated_task_ref,
                    claim_token,
                )
                return DelegatedWorkflowStepExecutionResponse(
                    status="deferred",
                    reason="no_persistent_effect",
                    workflowId=request.workflow_id,
                    stepKey=request.step_key,
                    retryable=True,
                    writeBackActions=write_back_actions,
                )
            await self._complete_delegated_event(
                start_event,
                delegated_task_ref,
                claim_token,
                force=True,
            )
            return DelegatedWorkflowStepExecutionResponse(
                status="ignored",
                reason="state_advanced_after_dispatch",
                workflowId=request.workflow_id,
                stepKey=request.step_key,
                writeBackActions=write_back_actions,
            )
        await self._complete_delegated_event(
            start_event,
            delegated_task_ref,
            claim_token,
            force=True,
        )
        return DelegatedWorkflowStepExecutionResponse(
            status="executed",
            reason="",
            workflowId=request.workflow_id,
            stepKey=request.step_key,
            writeBackActions=write_back_actions,
        )

    @staticmethod
    def _is_same_active_workflow_step(
        workflow: dict,
        request: DelegatedWorkflowStepExecutionRequest,
    ) -> bool:
        """判断执行后服务端是否仍停留在同一激活版本，用于区分已推进与暂时无效果。"""
        if str(workflow.get("status") or "").upper() != "RUNNING":
            return False
        steps = workflow.get("steps")
        for step in steps if isinstance(steps, list) else []:
            if not isinstance(step, dict):
                continue
            if str(step.get("stepKey") or "").strip() != request.step_key:
                continue
            try:
                activation_version = int(step.get("activationVersion") or 0)
            except (TypeError, ValueError):
                activation_version = 0
            return (
                str(step.get("taskId") or "").strip() == request.task_id
                and str(step.get("status") or "").upper() == "ACTIVE"
                and activation_version == request.activation_version
            )
        return False

    @staticmethod
    def _ignored_workflow_step_execution(
        request: DelegatedWorkflowStepExecutionRequest,
        reason: str,
    ) -> DelegatedWorkflowStepExecutionResponse:
        """把过期或已失效的 outbox 消息确认为已消费，避免产生任何业务副作用。"""
        return DelegatedWorkflowStepExecutionResponse(
            status="ignored",
            reason=reason,
            workflowId=request.workflow_id,
            stepKey=request.step_key,
        )

    async def handle_event(self, event: UnifiedEvent) -> OrchestratorResult:
        # 这个函数的作用是驱动单次事件从粗路由、设定命中、执行到回写的完整主流程。
        if self._is_desktop_workspace_command(event):
            return await self._handle_desktop_workspace_command(event)
        # 在任何模型调用前登记联系人新消息。相同会话后续抵达的消息会立即使旧推理失效，
        # 即使旧任务尚未完成解析、还没有拿到对应的 delegated_task 也不会继续发送。
        inbound_conversation_version = self._register_delegated_peer_inbound(event)
        delegated_task = await self._get_active_delegated_task(event)
        requested_execution_id = str(
            (event.raw_payload or {}).get("executionId")
            or (event.raw_payload or {}).get("commandId")
            or ""
        ).strip()
        execution_id = requested_execution_id or (str(delegated_task.get("id") or uuid4()) if delegated_task else str(uuid4()))
        if delegated_task:
            self._log_delegated_trace(
                "task_event_received",
                execution_id=execution_id,
                event=event,
                task_id=str(delegated_task.get("id") or ""),
                eventType=event.event_type,
                direction=event.direction,
                controlEvent=bool((event.raw_payload or {}).get("controlEvent")),
            )
        delegated_claim_token: str | None = None
        if delegated_task:
            # 显式工作流步骤会在进入主编排前认领事件。复用外层 token 可以避免这里
            # 再次认领同一个 eventId，并把租约的最终提交权保留给 outbox 执行入口。
            delegated_claim_token = str(
                (event.raw_payload or {}).get("_delegatedEventClaimToken") or ""
            ).strip() or await self._claim_delegated_event(event, delegated_task)
            if delegated_claim_token == "":
                self._log_delegated_trace(
                    "task_event_claim_skipped",
                    execution_id=execution_id,
                    event=event,
                    task_id=str(delegated_task.get("id") or ""),
                )
                return OrchestratorResult(
                    execution_id=execution_id,
                    status="success",
                    route="social_reply",
                    summary="Delegated event is already leased or completed.",
                    results=[],
                    final_reply="",
                    write_back_actions=["delegated_task_event:skipped"],
                    notification=None,
                    verified_memory_ids=[],
                )
        base_preliminary_route = "social_reply" if delegated_task else self.router.route(event)
        semantic_intent = (
            SemanticIntentDecision()
            if delegated_task
            else await self._classify_schedule_intent(event, base_preliminary_route)
        )
        preliminary_route = "social_reply" if delegated_task else (semantic_intent.route or base_preliminary_route)
        profile_match = await self._match_conversation_profile(event, preliminary_route)
        route = "social_reply" if delegated_task else self.router.route(event, profile_match)
        if not delegated_task and semantic_intent.route and not self._profile_has_preferred_route(profile_match):
            route = semantic_intent.route
        resolved_skills, unresolved_skill_references = self._resolve_skills(profile_match, route)
        resolved_model_result = await self._resolve_user_model_profile(event, route, profile_match)
        # 模型解析接口返回的是包装结果；媒体服务只接受其中真正的模型配置，不能传入包装对象。
        resolved_model_profile = resolved_model_result.profile if resolved_model_result else None
        if self.memory_candidate_extractor is not None:
            # 长期记忆提取独立于当前路由异步执行；未授权、非 OWNER 或无模型时内部会直接跳过。
            self.memory_candidate_extractor.schedule(event, profile_match, resolved_model_profile)
        # 主控台委托已经由任务决策图确定动作，不再交给旧 Planner 二次规划。
        # 这样可以避免历史 plan 中的额外 Agent 重复生成回复或重复调用发送工具。
        if delegated_task:
            execution_steps = (("social", "reply"), ("review", "review"))
            execution_mode = "delegated_react"
        else:
            plan = self.planner.build_plan(route)
            execution_steps = tuple((step.agent, step.action) for step in plan.steps)
            execution_mode = plan.mode
        current_media_analysis = []
        if route == "social_reply" and event.attachments:
            # 会话绑定模型负责生成回复；图片理解优先解析独立的视觉路由，避免纯文本模型收到 image_url 后报错。
            resolved_vision_model_result = await self._resolve_user_model_profile(
                event,
                "vision_analysis",
                profile_match,
                use_conversation_binding=False,
            )
            resolved_vision_model_profile = (
                resolved_vision_model_result.profile if resolved_vision_model_result and resolved_vision_model_result.matched else None
            )
            selected_vision_model_profile = self._select_vision_model_profile(
                resolved_model_profile,
                resolved_vision_model_profile,
            )
            # 只记录模型 ID，不记录 API Key；现场联调时可直接确认图片有没有误发给默认文本模型。
            logger.info(
                "视觉模型路由完成：eventId=%s, conversationModel=%s, routeModel=%s, selectedModel=%s",
                event.event_id,
                getattr(resolved_model_profile, "model", "") or "none",
                getattr(resolved_vision_model_profile, "model", "") or "none",
                getattr(selected_vision_model_profile, "model", "") or "none",
            )
            current_media_analysis = await self._analyze_attachments_for_social_reply(
                event,
                selected_vision_model_profile,
            )
        else:
            resolved_vision_model_result = None

        history_context = await self.memory.build_history_context(
            event,
            profile_match,
            # Skill 可能是跨多轮收集信息的工作流，记忆层需要预留更大的有效窗口。
            skill_context_enabled=bool(resolved_skills),
        )
        delegated_action = None
        if delegated_task:
            # 主控台委托先由任务图决定发送、等待或结束，不能先生成回复再补做状态判断。
            if (
                self._is_delegated_peer_inbound(event)
                and not await self._wait_for_latest_delegated_inbound(event, inbound_conversation_version)
            ):
                delegated_action = self._build_superseded_delegated_wait_action(event, delegated_task)
            else:
                delegated_action = await self._decide_delegated_task_action(
                    event=event,
                    task=delegated_task,
                    history_context=history_context,
                    model_profile=resolved_model_profile,
                )
            if delegated_action:
                self._log_delegated_trace(
                    "task_action_decided",
                    execution_id=execution_id,
                    event=event,
                    task_id=str(delegated_task.get("id") or ""),
                    action=delegated_action.action,
                    source=getattr(delegated_action, "source", ""),
                )
            if (
                delegated_action
                and self._is_delegated_write_back_superseded(event, delegated_task)
            ):
                logger.info(
                    "跳过已过期的委托状态写入：taskId=%s, eventId=%s",
                    delegated_task.get("id"),
                    event.event_id,
                )
                await self._complete_delegated_event(
                    event,
                    delegated_task,
                    delegated_claim_token,
                )
                return OrchestratorResult(
                    execution_id=execution_id,
                    status="success",
                    route=route,
                    summary="A newer contact message arrived; the older delegated decision was discarded.",
                    results=[],
                    final_reply="",
                    write_back_actions=["delegated_task_action:superseded"],
                    notification=None,
                    verified_memory_ids=[],
                )
            if delegated_action and delegated_action.action not in {"SEND_MESSAGE", "SEND_AND_COMPLETE"}:
                decision_result = AgentResult(
                    task_id=execution_id,
                    agent="delegated_task",
                    status="success",
                    structured_result=delegated_action.model_dump(by_alias=True),
                    reply_draft="",
                )
                persisted = await self._persist_delegated_task_decision(
                    event=event,
                    task=delegated_task,
                    decision=delegated_action,
                    results=[decision_result],
                )
                self._log_delegated_trace(
                    "task_action_persisted",
                    execution_id=execution_id,
                    event=event,
                    task_id=str(delegated_task.get("id") or ""),
                    action=delegated_action.action,
                    persisted=persisted,
                )
                action_name = delegated_action.action.lower()
                write_back_actions = [f"delegated_task_action:{action_name}"]
                if persisted:
                    write_back_actions.append(f"delegated_task_runtime_updated:{action_name}")
                await self._complete_delegated_event(
                    event,
                    delegated_task,
                    delegated_claim_token,
                )
                return OrchestratorResult(
                    execution_id=execution_id,
                    status="success",
                    route=route,
                    summary=f"Delegated task selected {delegated_action.action} before reply generation.",
                    results=[decision_result],
                    final_reply="",
                    write_back_actions=write_back_actions,
                    notification=None,
                    verified_memory_ids=[],
                )
        # 开放状态与 UI 代理进度分离：每轮从可信时间线重建，避免展示摘要变成 Agent 的事实来源。
        conversation_state = self.conversation_state_service.build(event, history_context)
        # 长期记忆与短期历史分开读取，只有用户确认且作用域匹配的事实能够进入本轮上下文。
        verified_memories = await self.memory.build_verified_memories(event)
        base_context = AgentTaskContext(
            task_id=execution_id,
            route=route,
            event=event,
            history_context=history_context,
            conversation_state=conversation_state,
            verified_memories=verified_memories,
            retrieved_knowledge=await self.memory.build_retrieved_knowledge(event, profile_match),
            allowed_tools=self._resolve_allowed_tools(profile_match, resolved_skills),
            execution_mode="confirm_required" if self._needs_human_confirmation(profile_match) else "suggest_only",
            metadata={
                "conversation_profile_match": profile_match.model_dump(by_alias=True) if profile_match else None,
                "resolved_model_profile": resolved_model_result.model_dump(by_alias=True) if resolved_model_result else None,
                "resolved_vision_model_profile": resolved_vision_model_result.model_dump(by_alias=True) if resolved_vision_model_result else None,
                "resolved_skills": [skill.model_dump(by_alias=True) for skill in resolved_skills],
                "unresolved_skill_references": unresolved_skill_references,
                "current_media_analysis": current_media_analysis,
                "semantic_schedule_intent": semantic_intent.model_dump(),
                "conversation_state": conversation_state.model_dump(by_alias=True),
                # 委托任务来自 Java 的用户隔离查询，SocialAgent 只能读取当前会话对应的这一条任务。
                "delegated_task": delegated_task,
                # SEND_MESSAGE 负责普通推进；SEND_AND_COMPLETE 负责先生成一条必要的纠正或收尾消息，
                # 待平台确认发送成功后再结束任务。其他动作都不会进入 SocialAgent。
                "delegated_task_action": (
                    delegated_action.model_dump(by_alias=True) if delegated_action else None
                ),
                # 执行轨迹只记录记忆 ID 和数量，避免把长期事实正文复制到日志。
                "verified_memory_ids": [memory.id for memory in verified_memories],
            },
        )

        results: list[AgentResult] = []
        previous_results: dict[str, dict] = {}
        for agent_name, action in execution_steps:
            # 普通路由执行 Planner 步骤；委托路由固定执行生成与审查，避免旧编排介入。
            agent = self.agents[agent_name]
            step_context = base_context.model_copy(
                update={
                    "metadata": {
                        **base_context.metadata,
                        "previous_results": previous_results,
                    }
                }
            )
            result = await agent.run(step_context, action)
            results.append(result)
            previous_results[agent_name] = result.structured_result

        handoff = next((result for result in results if result.structured_result.get("handoffRequired")), None)
        if handoff is not None:
            summary = str(handoff.structured_result.get("handoffSummary") or "需要你接管当前会话。")
            progress = str(handoff.structured_result.get("conversationProgress") or "")
            reason = str(handoff.structured_result.get("handoffReason") or "")
            proposed = str(handoff.structured_result.get("proposedDraft") or "")
            final_reply = "\n".join(part for part in (
                summary, progress, f"被拦截草稿：{proposed}" if proposed else "",
                f"触发原因：{reason}" if reason else ""
            ) if part)
        else:
            review = next((result for result in results if result.agent == "review"), None)
            approved_draft = str(review.structured_result.get("approvedDraft") or "").strip() if review else ""
            final_reply = approved_draft or "\n".join(result.reply_draft for result in results if result.reply_draft).strip()
        if not final_reply:
            final_reply = "No reply was generated."

        # 生成回复期间可能又抵达了同一会话的联系人消息。此时旧回复不应发送，
        # 最新事件会独立进入图并携带更完整的上下文。这里不写回旧状态，避免覆盖新一轮。
        if (
            delegated_task
            and delegated_action
            and delegated_action.action in {"SEND_MESSAGE", "SEND_AND_COMPLETE"}
            and self._is_delegated_write_back_superseded(event, delegated_task)
        ):
            logger.info(
                "跳过已过期的委托回复：taskId=%s, eventId=%s",
                delegated_task.get("id"),
                event.event_id,
            )
            await self._complete_delegated_event(
                event,
                delegated_task,
                delegated_claim_token,
            )
            return OrchestratorResult(
                execution_id=execution_id,
                status="success",
                route=route,
                summary="A newer contact message arrived; the older delegated reply was discarded.",
                results=results,
                final_reply="",
                write_back_actions=["delegated_task_action:superseded"],
                notification=None,
                verified_memory_ids=self._verified_memory_ids(verified_memories),
            )

        # 回复生成期间，ReAct 图已经更新了任务工作记忆。发送层必须读取这份最新状态，
        # 否则并发唤醒会看不到刚刚发送的内容，进而把相同消息再次写回 QQ。
        write_back_task = delegated_task
        if delegated_task and delegated_action and getattr(delegated_action, "state_json", ""):
            write_back_task = {
                **delegated_task,
                "stateJson": delegated_action.state_json,
            }
        write_back_actions = await self._write_back_if_needed(
            event,
            route,
            results,
            final_reply,
            profile_match,
            write_back_task,
        )
        if delegated_task:
            self._log_delegated_trace(
                "reply_writeback_finished",
                execution_id=execution_id,
                event=event,
                task_id=str(delegated_task.get("id") or ""),
                writeBackCount=len(write_back_actions),
                replyGenerated=bool(final_reply and final_reply != "No reply was generated."),
            )
        # 完成度判断在平台回写之后执行。即使模型判断或状态提交失败，也不能阻断已经生成的正常回复。
        task_completion = None
        if delegated_task:
            delegated_update = await self._update_delegated_task_runtime(
                event=event,
                task=delegated_task,
                delegated_action=delegated_action,
                history_context=history_context,
                final_reply=final_reply,
                write_back_actions=write_back_actions,
                model_profile=resolved_model_profile,
                results=results,
            )
            if delegated_update:
                status = str(delegated_update.get("status") or "ACTIVE").lower()
                write_back_actions.append(f"delegated_task_runtime_updated:{status}")
                self._log_delegated_trace(
                    "task_runtime_updated",
                    execution_id=execution_id,
                    event=event,
                    task_id=str(delegated_task.get("id") or ""),
                    status=status,
                )
        else:
            task_completion = await self._evaluate_task_completion(
                event,
                route,
                profile_match,
                history_context,
                final_reply,
                resolved_model_profile,
            )
        if task_completion and task_completion.get("requested"):
            write_back_actions.append("conversation_task_completion_requested")
        notification = self._build_notification_decision(results)
        if delegated_task:
            await self._complete_delegated_event(
                event,
                delegated_task,
                delegated_claim_token,
            )

        return OrchestratorResult(
            execution_id=execution_id,
            status="success",
            route=route,
            summary=f"Plan executed in {execution_mode} mode with {len(execution_steps)} step(s).",
            results=results,
            final_reply=final_reply,
            write_back_actions=write_back_actions,
            notification=notification,
            verified_memory_ids=self._collect_verified_memory_ids(verified_memories),
        )

    async def _get_active_delegated_task(self, event: UnifiedEvent) -> dict | None:
        """恢复当前会话的活动委托；服务异常时按无委托处理，避免普通消息链路整体不可用。"""
        if self.event_center_client is None:
            return None
        resolver = getattr(self.event_center_client, "get_active_delegated_task", None)
        if not callable(resolver):
            # 兼容尚未升级的客户端实现和只覆盖旧接口的测试替身，不把能力缺失误报成服务异常。
            return None
        try:
            return await resolver(event)
        except Exception as exception:
            logger.warning(
                "读取活动委托失败，继续使用普通路由：eventId=%s, error=%s",
                event.event_id,
                type(exception).__name__,
            )
            return None

    async def _claim_delegated_event(
        self,
        event: UnifiedEvent,
        task: dict,
    ) -> str | None:
        """抢占一条委托事件，避免多个 Runtime 实例重复执行同一轮副作用。

        返回 ``None`` 表示当前客户端尚未提供租约接口，主要用于兼容旧测试替身；
        返回空字符串表示租约已被其他执行占用，本轮必须直接退出。
        """
        if self.event_center_client is None:
            return None
        claim = getattr(self.event_center_client, "claim_delegated_task_event", None)
        if not callable(claim):
            return None
        task_id = str(task.get("id") or "").strip()
        event_id = self._stable_event_identity(event)
        if not task_id or not event_id:
            logger.warning(
                "委托事件缺少租约标识，拒绝执行：taskId=%s, eventId=%s",
                task_id,
                event_id,
            )
            return ""
        try:
            result = await claim(event, task_id, event_id, 120)
        except Exception as exception:
            logger.warning(
                "抢占委托事件失败，拒绝本轮执行：taskId=%s, eventId=%s, error=%s",
                task_id,
                event_id,
                type(exception).__name__,
            )
            return ""
        if not bool(result.get("claimed")) and str(result.get("status") or "").upper() == "COMPLETED":
            recover = getattr(
                self.event_center_client,
                "recover_dormant_delegated_task_event",
                None,
            )
            if callable(recover):
                try:
                    recovered = await recover(event, task_id, event_id)
                    if recovered:
                        logger.warning(
                            "已恢复旧版本遗留的空完成事件，重新认领一次：taskId=%s, eventId=%s",
                            task_id,
                            event_id,
                        )
                        result = await claim(event, task_id, event_id, 120)
                except Exception as exception:
                    logger.warning(
                        "恢复旧版本空完成事件失败：taskId=%s, eventId=%s, error=%s",
                        task_id,
                        event_id,
                        type(exception).__name__,
                    )
        if not bool(result.get("claimed")):
            logger.info(
                "委托事件已由其他执行占用：taskId=%s, eventId=%s, status=%s",
                task_id,
                event_id,
                result.get("status"),
            )
            return ""
        claim_token = str(result.get("claimToken") or "").strip()
        if not claim_token:
            logger.warning(
                "委托事件租约缺少 claimToken，拒绝执行：taskId=%s, eventId=%s",
                task_id,
                event_id,
            )
            return ""
        return claim_token

    async def _complete_delegated_event(
        self,
        event: UnifiedEvent,
        task: dict,
        claim_token: str | None,
        *,
        force: bool = False,
    ) -> None:
        """在本轮委托的状态写入和消息副作用全部完成后提交事件租约。"""
        if bool((event.raw_payload or {}).get("_deferDelegatedClaimCompletion")) and not force:
            return
        if not claim_token or self.event_center_client is None:
            return
        complete = getattr(self.event_center_client, "complete_delegated_task_event", None)
        if not callable(complete):
            return
        task_id = str(task.get("id") or "").strip()
        event_id = self._stable_event_identity(event)
        if not task_id or not event_id:
            return
        try:
            await complete(event, task_id, event_id, claim_token)
        except Exception as exception:
            # 平台副作用已经完成，提交失败只能保留租约等待超时，不能再次执行发送。
            logger.error(
                "提交委托事件租约失败：taskId=%s, eventId=%s, error=%s",
                task_id,
                event_id,
                type(exception).__name__,
            )

    async def _release_delegated_event(
        self,
        event: UnifiedEvent,
        task: dict,
        claim_token: str | None,
    ) -> None:
        """释放尚未产生持久化效果的委托事件，让 outbox 可以在稍后安全重试。"""
        if not claim_token or self.event_center_client is None:
            return
        release = getattr(self.event_center_client, "release_delegated_task_event", None)
        if not callable(release):
            return
        task_id = str(task.get("id") or "").strip()
        event_id = self._stable_event_identity(event)
        if not task_id or not event_id:
            return
        try:
            await release(event, task_id, event_id, claim_token)
        except Exception as exception:
            logger.error(
                "释放委托事件租约失败：taskId=%s, eventId=%s, error=%s",
                task_id,
                event_id,
                type(exception).__name__,
            )

    async def _persist_current_event(self, event: UnifiedEvent, task: dict) -> None:
        """在每次 LangGraph 执行前把当前入站事件写入 L0，保证当前事件不丢失。

        写入失败只记录诊断，不阻断推理：Java 事件表本身已在投递时持久化过该事件，
        L0 是给“历史查询失败时仍能继续推理”提供的第二层事实源。
        """
        if self.event_center_client is None:
            return
        upsert = getattr(self.event_center_client, "upsert_delegated_task_current_event", None)
        if not callable(upsert):
            return
        task_id = str(task.get("id") or "").strip()
        if not task_id:
            return
        try:
            await upsert(event, task_id)
        except Exception as exception:
            logger.warning(
                "写入 L0 当前事件失败，继续使用内存事件推理：taskId=%s, eventId=%s, errorType=%s, message=%s",
                task_id,
                event.event_id,
                type(exception).__name__,
                str(exception),
            )

    async def _load_current_event_for_task(self, event: UnifiedEvent, task: dict) -> dict | None:
        """历史查询失败时读取 L0 当前事件作为推理兜底；读取失败则返回 None。"""
        if self.event_center_client is None:
            return None
        reader = getattr(self.event_center_client, "get_delegated_task_current_event", None)
        if not callable(reader):
            return None
        task_id = str(task.get("id") or "").strip()
        if not task_id:
            return None
        try:
            stored = await reader(event, task_id)
        except Exception as exception:
            logger.warning(
                "读取 L0 当前事件失败：taskId=%s, eventId=%s, errorType=%s, message=%s",
                task_id,
                event.event_id,
                type(exception).__name__,
                str(exception),
            )
            return None
        if not isinstance(stored, dict) or not stored.get("eventId"):
            return None
        payload = stored.get("payload")
        if isinstance(payload, dict):
            return payload
        payload_json = stored.get("payloadJson")
        if isinstance(payload_json, str) and payload_json.strip():
            parsed = self._parse_json_object(payload_json)
            if parsed:
                return parsed
        return {
            "eventId": stored.get("eventId"),
            "eventType": stored.get("eventType") or "message",
            "text": stored.get("text") or "",
            "sentAt": stored.get("occurredAt"),
        }

    async def _load_delegated_task_history(
        self,
        event: UnifiedEvent,
        fallback_history: list[dict],
        task: dict,
    ) -> list[dict]:
        """读取委托任务的可信双方时间线；服务异常时用当前事件继续推理。"""
        history = self._filter_delegated_history_for_event(fallback_history, event, task)
        task_id = str(task.get("id") or "").strip()
        task_state = self._delegated_task_state(task)
        platform, chat_type, chat_id = self._delegated_step_scope(task)
        if not all((platform, chat_type, chat_id)):
            platform, chat_type, chat_id = self._conversation_scope_from_row(
                event.model_dump(by_alias=True)
            )
        started_at, _start_event_id = self._delegated_step_watermark(task, task_state)
        query_after = (
            started_at
            or str(task_state.get("taskCreatedAt") or task.get("createdAt") or "").strip()
            or None
        )
        if self.event_center_client is None:
            return history
        rows: list[dict] = []
        try:
            rows = await self.event_center_client.list_conversation_messages(
                chat_id,
                platform=platform,
                chat_type=chat_type,
                # L1 读取步骤开始后的全部消息；长期事实由 stateJson 的滚动记忆承载。
                limit=500,
                user_id=self._resolve_event_user_id(event),
                after=query_after or None,
            )
            return self._filter_delegated_history_for_event(rows, event, task)
        except Exception as exception:
            logger.warning(
                "委托历史查询失败，改用当前事件继续推理。taskId=%s | %s",
                task_id,
                self._history_failure_diagnostic(
                    exception=exception,
                    scope=(platform, chat_type, chat_id),
                    after=query_after,
                    before=None,
                    limit=500,
                    rows=rows,
                ),
            )
            current_event = await self._load_current_event_for_task(event, task)
            if current_event:
                merged = self._merge_current_event_into_history(history, current_event)
                if merged:
                    return merged
            return history

    async def _load_delegated_task_pre_history(
        self,
        event: UnifiedEvent,
        task: dict,
    ) -> list[dict]:
        """按需读取起点前的有限窗口（L2），只作为背景，不能作为任务完成证据。"""
        if self.event_center_client is None or not self._delegated_task_history_access_allowed(task):
            return []
        task_state = self._delegated_task_state(task)
        platform, chat_type, chat_id = self._delegated_step_scope(task)
        if not all((platform, chat_type, chat_id)):
            platform, chat_type, chat_id = self._conversation_scope_from_row(
                event.model_dump(by_alias=True)
            )
        started_at, _start_event_id = self._delegated_step_watermark(task, task_state)
        query_before = (
            started_at
            or str(task_state.get("taskCreatedAt") or task.get("createdAt") or "").strip()
        )
        if not query_before:
            return []
        rows: list[dict] = []
        try:
            rows = await self.event_center_client.list_conversation_messages(
                chat_id,
                platform=platform,
                chat_type=chat_type,
                limit=30,
                user_id=self._resolve_event_user_id(event),
                before=query_before,
            )
            return self._filter_delegated_history_for_event(rows, event, task)
        except Exception as exception:
            logger.warning(
                "读取任务前背景失败，继续使用任务内记忆。taskId=%s | %s",
                task.get("id"),
                self._history_failure_diagnostic(
                    exception=exception,
                    scope=(platform, chat_type, chat_id),
                    after=None,
                    before=query_before,
                    limit=30,
                    rows=rows,
                ),
            )
            return []

    @classmethod
    def _history_failure_diagnostic(
        cls,
        *,
        exception: Exception,
        scope: tuple[str, str, str],
        after: str | None,
        before: str | None,
        limit: int,
        rows: list[dict],
    ) -> str:
        """把历史接口异常整理成可排查的完整诊断，不允许只显示“获取历史失败”。"""
        http_status = ""
        response_body = ""
        if hasattr(exception, "response") and exception.response is not None:
            response = exception.response
            http_status = str(getattr(response, "status_code", ""))
            response_body = " ".join(str(getattr(response, "text", "") or "").split())[:500]
        return (
            "请求范围=platform=" + str(scope[0] or "-")
            + ",chatType=" + str(scope[1] or "-")
            + ",chatId=" + str(scope[2] or "-")
            + ",limit=" + str(limit)
            + ",after=" + str(after or "-")
            + ",before=" + str(before or "-")
            + " | httpStatus=" + (http_status or "-")
            + " | responseBody=" + (response_body or "-")
            + " | dbHitCount=" + cls._extract_diagnostic_count(response_body, "dbHitCount")
            + " | filteredAfter=" + cls._extract_diagnostic_count(response_body, "filteredAfter")
            + " | localRows=" + str(len(rows) if isinstance(rows, list) else -1)
            + " | errorType=" + type(exception).__name__
            + " | message=" + " ".join(str(exception).split())[:300]
        )

    @staticmethod
    def _extract_diagnostic_count(response_body: str, key: str) -> str:
        """从 Java 失败响应正文中提取 dbHitCount / filteredAfter 等诊断计数。"""
        marker = key + "="
        if marker in response_body:
            tail = response_body.split(marker, 1)[1].strip()
            value = tail.split(",")[0].strip()
            return value
        return "-"

    @classmethod
    def _merge_current_event_into_history(
        cls,
        history: list[dict],
        current_event: dict,
    ) -> list[dict]:
        """把 L0 当前事件无条件并入时间线，历史接口失败时仍能继续推理。"""
        if not isinstance(current_event, dict) or not current_event.get("eventId"):
            return history
        event_id = str(current_event.get("eventId") or "").strip()
        for row in history:
            row_id = str(
                row.get("eventId")
                or row.get("event_id")
                or row.get("platformMessageId")
                or row.get("platform_message_id")
                or ""
            ).strip()
            if row_id == event_id:
                return history
        normalized = {
            "eventId": event_id,
            "platformMessageId": str(current_event.get("platformMessageId") or "").strip(),
            "clientMessageId": str(current_event.get("clientMessageId") or "").strip(),
            "at": str(
                current_event.get("sentAt")
                or current_event.get("timestamp")
                or current_event.get("occurredAt")
                or current_event.get("receivedAt")
                or ""
            ).strip(),
            "text": " ".join(
                str(current_event.get("text") or current_event.get("content") or "").split()
            ),
            "eventType": str(current_event.get("eventType") or "message").lower(),
            "direction": str(current_event.get("direction") or "").upper(),
            "actorType": str(current_event.get("actorType") or "").upper(),
            "messageOrigin": str(current_event.get("messageOrigin") or "").upper(),
            "rawPayload": current_event.get("rawPayload") if isinstance(current_event.get("rawPayload"), dict) else {},
            "platform": str(current_event.get("platform") or "").strip(),
            "chatType": str(current_event.get("chatType") or "").strip(),
            "chatId": str(current_event.get("chatId") or "").strip(),
            "sender": current_event.get("sender") if isinstance(current_event.get("sender"), dict) else {},
        }
        return [*history, normalized]

    @classmethod
    def _delegated_step_scope(cls, task: dict) -> tuple[str, str, str]:
        """从步骤固化的 conversationScopeJson 恢复会话范围；非法 JSON 时回退平台字段。"""
        raw = task.get("conversationScopeJson") or task.get("conversation_scope_json") or task.get("conversationScope")
        if isinstance(raw, str) and raw.strip():
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict):
                platform = str(parsed.get("platform") or "").strip()
                chat_type = str(
                    parsed.get("chatType") or parsed.get("chat_type") or ""
                ).strip().lower()
                chat_id = str(parsed.get("chatId") or parsed.get("chat_id") or "").strip()
                if platform and chat_type and chat_id:
                    return platform, chat_type, chat_id
        return cls._conversation_scope_from_row(task)

    @staticmethod
    def _delegated_step_watermark(task: dict, state: dict) -> tuple[str, str]:
        """返回步骤起点水位（启动时间、起点事件 ID），供 L1/L2 历史窗口使用。"""
        started_at = str(
            task.get("startedAt")
            or task.get("started_at")
            or state.get("taskStartedAt")
            or state.get("taskCreatedAt")
            or task.get("createdAt")
            or ""
        ).strip()
        start_event_id = str(
            task.get("startEventId") or task.get("start_event_id") or ""
        ).strip()
        return started_at, start_event_id

    @staticmethod
    def _delegated_task_state(task: dict) -> dict:
        """兼容 Java 返回的 JSON 字符串与测试场景中的对象状态。"""
        raw_state = task.get("stateJson")
        if isinstance(raw_state, dict):
            return raw_state
        if not isinstance(raw_state, str) or not raw_state.strip():
            return {}
        try:
            parsed = json.loads(raw_state)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _conversation_scope_from_row(row: dict) -> tuple[str, str, str]:
        """从事件、历史行或任务对象读取会话范围，兼容 Java/OneBot 字段命名。"""
        payload = row.get("rawPayload") if isinstance(row.get("rawPayload"), dict) else {}
        platform = str(row.get("platform") or payload.get("platform") or "").strip().lower()
        chat_type = str(
            row.get("chatType")
            or row.get("chat_type")
            or payload.get("chatType")
            or payload.get("chat_type")
            or ""
        ).strip().lower()
        chat_id = str(
            row.get("chatId")
            or row.get("chat_id")
            or payload.get("chatId")
            or payload.get("chat_id")
            or payload.get("group_id")
            or payload.get("user_id")
            or ""
        ).strip()
        return platform, chat_type, chat_id

    @classmethod
    def _filter_delegated_history_for_event(
        cls,
        rows: list[dict] | None,
        event: UnifiedEvent,
        task: dict,
    ) -> list[dict]:
        """只保留当前目标会话的双方消息，阻断跨会话昵称和事实污染。"""
        expected = cls._conversation_scope_from_row(event.model_dump(by_alias=True))
        task_scope = cls._conversation_scope_from_row(task)
        filtered: list[dict] = []
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            if not cls._delegated_history_row_matches_scope(row, expected, task_scope):
                continue
            filtered.append(row)
        return filtered

    @classmethod
    def _delegated_history_row_matches_scope(
        cls,
        row: dict,
        expected_scope: tuple[str, str, str],
        task_scope: tuple[str, str, str],
    ) -> bool:
        """判断一条历史或状态行是否属于当前委托会话。

        任务运行期产生的新时间线都会带完整 scope。仅为兼容升级前的旧状态，才允许
        完全没有 scope 的行继续用于“任务自身 scope 与当前事件 scope 一致”的任务；
        缺一部分字段的行无法可靠归属，必须拒绝，不能猜测后混入其他私聊或群聊。
        """
        row_scope = cls._conversation_scope_from_row(row)
        if all(row_scope):
            return row_scope == expected_scope
        return row_scope == ("", "", "") and task_scope == expected_scope

    @classmethod
    def _delegated_task_history_access_allowed(cls, task: dict) -> bool:
        """读取主控台任务的历史授权，默认只允许按需读取有限背景。"""
        state = cls._delegated_task_state(task)
        value = task.get(
            "historyAccessAllowed",
            task.get("allowPreTaskHistory", state.get("historyAccessAllowed", True)),
        )
        return str(value).strip().lower() not in {"false", "0", "no", "off"}

    async def _decide_delegated_task_action(
        self,
        *,
        event: UnifiedEvent,
        task: dict,
        history_context: list[dict],
        model_profile,
    ):
        """在 SocialAgent 运行前恢复背景并决定发送、等待或结束。"""
        task_id = str(task.get("id") or "").strip()
        # 每次执行前先把当前入站事件写入 L0，历史接口失败时仍能基于它继续推理。
        await self._persist_current_event(event, task)
        history = await self._load_delegated_task_history(event, history_context, task)
        task_state = self._delegated_task_state(task)
        pre_task_history = list(task_state.get("preTaskHistory") or [])
        history_access_allowed = self._delegated_task_history_access_allowed(task)
        try:
            decision = await self.delegated_task_workflow.decide_action(
                DelegatedTaskActionInput(
                    task=task,
                    history=history,
                    event=event.model_dump(by_alias=True),
                    preTaskHistory=pre_task_history,
                    historyAccessAllowed=history_access_allowed,
                    contextEnvelope=self._build_delegated_context_envelope(
                        event=event,
                        task=task,
                        task_history=history,
                        pre_task_history=pre_task_history,
                    ),
                ),
                model_profile,
            )
            # 只有模型显式请求、用户已授权且尚未缓存时，才读取起点前的有限背景（L2）。
            if (
                decision.requested_tool == "get_task_pre_history"
                and history_access_allowed
                and not pre_task_history
            ):
                pre_task_history = await self._load_delegated_task_pre_history(event, task)
                decision = await self.delegated_task_workflow.decide_action(
                    DelegatedTaskActionInput(
                        task=task,
                        history=history,
                        event=event.model_dump(by_alias=True),
                        preTaskHistory=pre_task_history,
                        # 防止同一轮因为背景不足无限请求历史。
                        historyAccessAllowed=False,
                        contextEnvelope=self._build_delegated_context_envelope(
                            event=event,
                            task=task,
                            task_history=history,
                            pre_task_history=pre_task_history,
                        ),
                    ),
                    model_profile,
                )
            return decision
        except Exception as exception:
            logger.warning(
                "委托动作决策失败，本轮安全等待：taskId=%s, eventId=%s, error=%s",
                task_id,
                event.event_id,
                type(exception).__name__,
            )
            # 决策图异常时不能伪造 SEND_MESSAGE。等待会保留任务和当前记忆，
            # 下一条可信事件到达后再由模型重新决定，避免故障被放大成重复发送。
            return DelegatedTaskActionDecision(
                action="WAIT",
                reason="决策图不可用，本轮不执行外部副作用",
                progressSummary=str(task.get("progressSummary") or "等待下一次可靠决策"),
                stateJson=str(task.get("stateJson") or "{}"),
                lastEventId=self._stable_event_identity(event),
                requestedTool="update_delegated_task",
            )

    def _build_delegated_context_envelope(
        self,
        *,
        event: UnifiedEvent,
        task: dict,
        task_history: list[dict],
        pre_task_history: list[dict],
    ) -> dict:
        """用统一 ContextAssembler 组装一次性可信上下文，状态图只消费该结果。"""
        assembler = self._delegated_context_assembler()
        return assembler.assemble(
            event=event.model_dump(by_alias=True),
            task=task,
            task_history=task_history,
            pre_task_history=pre_task_history or None,
        )

    def _delegated_context_assembler(self):
        """延迟创建统一上下文组装器，避免在无委托任务的普通链路中承担初始化成本。"""
        assembler = getattr(self, "_context_assembler", None)
        if assembler is None:
            assembler = DelegatedTaskContextAssembler()
            object.__setattr__(self, "_context_assembler", assembler)
        return assembler

    @staticmethod
    def _is_delegated_peer_inbound(event: UnifiedEvent) -> bool:
        """在任务图异常回退时识别真实联系人消息，规则与任务图保持一致。

        新版连接器会提供 direction、actorType 或 messageOrigin，可直接据此判断。
        旧版 NapCat 私聊事件可能完全缺少这些字段，因此还需要通过会话身份判断：
        发送者必须等于私聊 chatId，并且不能等于当前登录账号 selfId。这个兼容规则
        只应用于私聊，避免把群成员消息或机器人自身回显误当成委托任务的联系人回复。
        """
        event_type = str(event.event_type or "").lower()
        direction = str(event.direction or "").upper()
        actor = str(event.actor_type or "").upper()
        raw_payload = event.raw_payload or {}
        origin = str(raw_payload.get("messageOrigin") or "").upper()
        sender_id = str(event.sender.id if event.sender else "").strip()
        self_id = str(event.self_id or "").strip()
        chat_id = str(event.chat_id or "").strip()
        chat_type = str(event.chat_type or "").strip().lower()
        if not (event.text or "").strip() or event_type != "message":
            return False
        event_row = event.model_dump(by_alias=True)
        if is_runtime_generated_message(event_row):
            return False
        if direction == "OUTBOUND" or actor in {"AGENT", "SYSTEM", "SELF"}:
            return False
        if origin in {
            "INTERNAL",
            "AGENT",
            "AGENT_AUTO",
            "AGENT_CONFIRMED",
            "USER_MANUAL",
        }:
            return False
        # 即使旧连接器没有方向字段，只要能确认发送者是登录账号本身，也必须拒绝。
        if sender_id and self_id and sender_id == self_id:
            return False
        if direction == "INBOUND" or actor in {"CONTACT", "PEER", "REMOTE"}:
            return True
        if origin in {"EXTERNAL", "PLATFORM"}:
            return True
        # 真实 NapCat 私聊载荷的兼容路径：sender 是该会话联系人，而不是当前账号。
        return bool(
            chat_type == "private"
            and sender_id
            and self_id
            and chat_id
            and sender_id == chat_id
            and sender_id != self_id
        )

    @staticmethod
    def _stable_event_identity(event: UnifiedEvent) -> str:
        """返回平台重投期间保持不变的事件身份，供并发合并和工具幂等使用。"""
        return canonical_message_identity(
            event.model_dump(by_alias=True),
            str(event.text or ""),
        )

    @staticmethod
    def _normalize_outbound_text(value: str) -> str:
        """规范化待发送文本，使空白、全半角和大小写差异不会绕过重复检测。"""
        normalized = unicodedata.normalize("NFKC", str(value or ""))
        return "".join(normalized.split()).casefold()

    @classmethod
    def _outbound_payload_digest(cls, payload: dict) -> str:
        """计算消息内容摘要；同一内容在不同事件中仍会得到相同摘要。"""
        message = payload.get("message")
        if isinstance(message, str):
            source = message
        else:
            segments = payload.get("segments") or message or []
            source = json.dumps(
                segments,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        normalized = cls._normalize_outbound_text(source)
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]

    @staticmethod
    def _conversation_idempotency_scope(event: UnifiedEvent) -> str:
        """返回会话级发送作用域，供主控台并行委托共享幂等键。

        主控台的一条自然语言命令可能被拆成多个目标会话任务，也可能因重试短暂出现
        同一会话的并行任务。这里不能再使用任务 ID 作为发送作用域，否则每个任务都会
        拥有独立的发送键并重复写入 QQ。普通设定集回复不使用此作用域，避免把不同轮次
        的正常重复表达永久抑制。
        """
        identity = ":".join(
            (
                str(event.platform or "unknown").strip().lower(),
                str(event.chat_type or "unknown").strip().lower(),
                str(event.chat_id or "unknown").strip(),
            )
        )
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]

    @classmethod
    def _delegated_turn_anchor(cls, event: UnifiedEvent, task: dict | None) -> str:
        """确定当前对话轮次。

        新的联系人入站消息会开启新轮次；没有新入站时，任务可主动发送不同内容，
        但同一内容始终落到同一个幂等键，避免并发事件造成重复发送。
        """
        state = cls._delegated_task_state(task or {})
        expected_scope = cls._conversation_scope_from_row(event.model_dump(by_alias=True))
        task_scope = cls._conversation_scope_from_row(task or {})
        timeline = state.get("timeline")
        if isinstance(timeline, list):
            for item in reversed(timeline):
                if not isinstance(item, dict):
                    continue
                if not cls._delegated_history_row_matches_scope(
                    item,
                    expected_scope,
                    task_scope,
                ):
                    continue
                direction = str(item.get("direction") or "").upper()
                actor = str(item.get("actorType") or "").upper()
                role = str(item.get("role") or "").lower()
                if direction != "INBOUND" and actor not in {"CONTACT", "PEER"} and role not in {
                    "peer",
                    "contact",
                    "other",
                }:
                    continue
                identity = next(
                    (
                        str(item.get(key) or "").strip()
                        for key in ("eventId", "platformMessageId", "clientMessageId", "at")
                        if str(item.get(key) or "").strip()
                    ),
                    "",
                )
                if identity:
                    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]

        if task and cls._is_delegated_peer_inbound(event):
            identity = cls._stable_event_identity(event)
        else:
            # 没有新的对方消息时仍允许 Agent 主动发言，但同一会话的并行任务必须共享
            # 同一轮次锚点，不能因为任务 ID 不同而绕过下方的幂等控制。
            identity = "conversation-open"
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]

    @classmethod
    def _outbound_already_recorded(cls, task: dict | None, turn_anchor: str, digest: str) -> bool:
        """检查持久化任务状态，防止进程重启后再次发送同一轮的相同内容。"""
        ledger = cls._delegated_task_state(task or {}).get("outboundLedger")
        if not isinstance(ledger, list):
            return False
        return any(
            isinstance(item, dict)
            and str(item.get("turnAnchor") or "") == turn_anchor
            and str(item.get("contentDigest") or "") == digest
            for item in ledger
        )

    def _register_delegated_peer_inbound(self, event: UnifiedEvent) -> int | None:
        """登记联系人消息版本，使同一会话的旧推理在发送前自动失效。

        版本键只使用 platform、chatType 与 chatId，不能绑定任务 ID。用户可在同一联系人
        会话中新建、结束或切换任务；若版本仍绑定任务，旧任务的模型调用会绕过防抖继续发送。
        """
        if not self._is_delegated_peer_inbound(event):
            return None
        execution_key = self._delegated_conversation_execution_scope(event)
        if not execution_key:
            return None
        event_id = self._stable_event_identity(event)
        if self._delegated_conversation_latest_event_ids.get(execution_key) == event_id:
            return self._delegated_conversation_versions.get(execution_key, 0)
        version = self._delegated_conversation_versions.get(execution_key, 0) + 1
        self._delegated_conversation_versions[execution_key] = version
        self._delegated_conversation_latest_event_ids[execution_key] = event_id
        return version

    async def _wait_for_latest_delegated_inbound(
        self,
        event: UnifiedEvent,
        registered_version: int | None,
    ) -> bool:
        """等待短暂合并窗口后，确认当前消息仍是该会话最新的一条联系人消息。"""
        execution_key = self._delegated_conversation_execution_scope(event)
        if not execution_key or registered_version is None:
            return True
        await self.sleeper(self._delegated_inbound_debounce_seconds)
        return (
            self._delegated_conversation_versions.get(execution_key) == registered_version
            and self._delegated_conversation_latest_event_ids.get(execution_key)
            == self._stable_event_identity(event)
        )

    @classmethod
    def _delegated_conversation_execution_scope(cls, event: UnifiedEvent) -> str:
        """生成会话级执行键，隔离不同私聊和群聊的异步推理状态。"""
        platform, chat_type, chat_id = cls._conversation_scope_from_row(event.model_dump(by_alias=True))
        if not all((platform, chat_type, chat_id)):
            return ""
        return f"{platform}:{chat_type}:{chat_id}"

    def _is_latest_delegated_inbound(self, event: UnifiedEvent) -> bool:
        """确认当前联系人消息仍是该会话最新事件，防止过期推理写入平台。"""
        execution_key = self._delegated_conversation_execution_scope(event)
        if not execution_key:
            return True
        latest_event_id = self._delegated_conversation_latest_event_ids.get(execution_key)
        return not latest_event_id or latest_event_id == self._stable_event_identity(event)

    def _is_delegated_write_back_superseded(self, event: UnifiedEvent, task: dict | None) -> bool:
        """判断委托任务回写是否已经被同一会话里的更新联系人消息覆盖。"""
        if not task:
            return False
        return self._is_delegated_peer_inbound(event) and not self._is_latest_delegated_inbound(event)

    @staticmethod
    def _build_superseded_delegated_wait_action(
        event: UnifiedEvent,
        task: dict,
    ) -> DelegatedTaskActionDecision:
        """为被后续联系人消息覆盖的事件生成等待决策，阻止旧消息继续进入回复链路。"""
        return DelegatedTaskActionDecision(
            action="WAIT",
            reason="同一会话收到更新的联系人消息，本事件已合并到最新上下文",
            progressSummary=str(task.get("progressSummary") or "等待合并后的最新联系人消息"),
            stateJson=str(task.get("stateJson") or "{}"),
            lastEventId=self._stable_event_identity(event),
            requestedTool="update_delegated_task",
        )

    async def _persist_delegated_task_decision(
        self,
        *,
        event: UnifiedEvent,
        task: dict,
        decision,
        results: list[AgentResult] | None = None,
    ) -> dict | None:
        """通过受控任务工具持久化 WAIT 或 COMPLETE_TASK 决策，并记录审计调用。"""
        if self.event_center_client is None:
            return None
        task_id = str(task.get("id") or "").strip()
        if not task_id:
            return None
        requested_tool = str(decision.requested_tool or "update_delegated_task")
        if requested_tool not in {"update_delegated_task", "complete_delegated_task"}:
            raise ValueError(f"unsupported delegated task state tool: {requested_tool}")
        workflow_id = str(task.get("workflowId") or "").strip()
        step_key = str(task.get("stepKey") or "").strip()
        is_workflow_completion = (
            requested_tool == "complete_delegated_task"
            and bool(workflow_id)
            and bool(step_key)
        )
        tool_context = ToolExecutionContext(
            user_id=self._resolve_event_user_id(event),
            event_id=event.event_id,
            task_id=task_id,
            allowed_tools=frozenset({"update_delegated_task", "complete_delegated_task"}),
        )
        # decision 可能来自兼容分支并携带临时 eventId。任务状态和工具副作用统一使用
        # 平台稳定消息身份，确保同一条 QQ 消息被 Webhook/MQ 重投时只提交一次。
        stable_event_id = self._stable_event_identity(event)
        idempotency_key = f"delegated:{task_id}:{stable_event_id}:{requested_tool}"
        try:
            if is_workflow_completion:
                try:
                    produced_facts = self._delegated_workflow_produced_facts(
                        task,
                        decision,
                        event,
                    )
                except DelegatedWorkflowFactsMissingError as exception:
                    # 模型判断任务完成但没有给齐父工作流声明事实时，不能让消息入口返回
                    # 500。保留当前任务并明确记录缺口，后续联系人消息仍可继续推进。
                    waiting_state = self._parse_json_object(
                        getattr(decision, "state_json", "")
                    )
                    waiting_state["workflowCompletionPending"] = {
                        "missingFacts": list(exception.missing_facts),
                        "eventId": stable_event_id,
                        "reason": str(exception),
                    }
                    waiting_key = (
                        f"delegated:{task_id}:{stable_event_id}:workflow-facts-wait"
                    )
                    response = await self.tools.ainvoke(
                        "update_delegated_task",
                        context=tool_context,
                        idempotency_key=waiting_key,
                        arguments={
                            "event": event.model_dump(mode="json"),
                            "task_id": task_id,
                            "progress_summary": (
                                "等待补充父工作流事实："
                                + "、".join(exception.missing_facts)
                            ),
                            "state_json": json.dumps(
                                waiting_state,
                                ensure_ascii=False,
                            ),
                            "last_event_id": stable_event_id,
                            "completion_report": "",
                        },
                    )
                    if results:
                        results[-1].tool_calls.append(
                            ToolCallRecord(
                                tool="update_delegated_task",
                                arguments={
                                    "taskId": task_id,
                                    "action": "WAIT",
                                    "missingFacts": list(exception.missing_facts),
                                    "idempotencyKey": waiting_key,
                                },
                            )
                        )
                    return response
                completion_report = str(getattr(decision, "completion_report", "") or "")
                progress_summary = str(getattr(decision, "progress_summary", "") or "")
                # 把声明事实发布为类型化产物：type 由事实键推导，sourceEventId 指向触发事件，
                # Java 在同一事务内原子保存产物、完成上游步骤并唤醒下游步骤。
                artifacts = [
                    {
                        "type": str(key).strip().upper(),
                        "name": key,
                        "value": value,
                        "sourceEventId": stable_event_id,
                    }
                    for key, value in produced_facts.items()
                ]
                response = await self.event_center_client.complete_delegated_workflow_step(
                    event,
                    workflow_id,
                    step_key,
                    produced_facts=produced_facts,
                    result_summary=completion_report or progress_summary,
                    result={
                        "taskId": task_id,
                        "action": getattr(decision, "action", "COMPLETE_TASK"),
                        "evidence": list(getattr(decision, "evidence", []) or []),
                        "state": self._parse_json_object(getattr(decision, "state_json", "")),
                        "completionReport": completion_report,
                    },
                    artifacts=artifacts,
                    source_event_id=stable_event_id,
                )
            else:
                response = await self.tools.ainvoke(
                    requested_tool,
                    context=tool_context,
                    idempotency_key=idempotency_key,
                    arguments={
                        "event": event.model_dump(mode="json"),
                        "task_id": task_id,
                        "progress_summary": decision.progress_summary,
                        "state_json": decision.state_json,
                        "last_event_id": stable_event_id,
                        "completion_report": decision.completion_report,
                    },
                )
            if results:
                results[-1].tool_calls.append(
                    ToolCallRecord(
                        tool=(
                            "complete_delegated_workflow_step"
                            if is_workflow_completion
                            else requested_tool
                        ),
                        arguments={
                            "taskId": task_id,
                            "action": getattr(decision, "action", "RUNTIME_UPDATE"),
                            "evidence": list(getattr(decision, "evidence", []) or []),
                            "idempotencyKey": idempotency_key,
                        },
                    )
                )
            return response
        except DelegatedWorkflowCompletionError:
            raise
        except Exception as exception:
            if is_workflow_completion:
                raise DelegatedWorkflowCompletionError(
                    "父工作流步骤完成回调失败: "
                    f"workflowId={workflow_id}, stepKey={step_key}"
                ) from exception
            logger.warning(
                "委托任务状态持久化失败，保留数据库原状态：taskId=%s, eventId=%s, error=%s",
                task_id,
                event.event_id,
                type(exception).__name__,
            )
            return None

    @staticmethod
    def _parse_json_object(value: object) -> dict[str, Any]:
        """解析状态 JSON；无效或非对象内容统一返回空对象。"""
        if isinstance(value, dict):
            return dict(value)
        if not isinstance(value, str) or not value.strip():
            return {}
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @classmethod
    def _delegated_workflow_produced_facts(
        cls,
        task: dict,
        decision,
        event: UnifiedEvent | None = None,
    ) -> dict[str, Any]:
        """提取父步骤声明事实，并从单一事实型联系人回复中恢复模型漏填的值。"""
        declared = task.get("producesFacts")
        fact_keys = (
            [str(item).strip() for item in declared if str(item).strip()]
            if isinstance(declared, list)
            else []
        )
        if not fact_keys:
            return {}

        state = cls._parse_json_object(getattr(decision, "state_json", ""))
        sources = [state.get("producedFacts"), state.get("knownFacts"), state]
        facts: dict[str, Any] = {}
        missing: list[str] = []
        for key in fact_keys:
            for source in sources:
                if isinstance(source, dict) and key in source:
                    facts[key] = source[key]
                    break
            else:
                missing.append(key)

        # 对“询问一个信息，再转告给下一联系人”这类步骤，当前联系人原话就是唯一
        # 声明事实的可靠来源。这里按事实槽位数量处理，不依赖时间、课程等业务词。
        if (
            missing
            and len(fact_keys) == 1
            and event is not None
            and cls._is_delegated_peer_inbound(event)
        ):
            reply_text = " ".join(str(event.text or "").split()).strip()
            if reply_text:
                facts[fact_keys[0]] = reply_text
                missing.clear()
        if missing:
            raise DelegatedWorkflowFactsMissingError(missing)
        return facts

    async def _update_delegated_task_runtime(
        self,
        *,
        event: UnifiedEvent,
        task: dict,
        delegated_action,
        history_context: list[dict],
        final_reply: str,
        write_back_actions: list[str],
        model_profile,
        results: list[AgentResult] | None = None,
    ) -> dict | None:
        """记录已经执行的发送结果，并原子处理“发送收尾消息后结束”动作。

        普通 SEND_MESSAGE 仍交给运行图记录本轮进展。SEND_AND_COMPLETE 则必须先确认
        QQ 平台回写成功，再使用任务图已经生成并校验过的完成决策提交 COMPLETED；
        若消息发送失败，不能结束任务，而是回落到普通运行状态更新，等待下一轮重试。
        """
        if self.event_center_client is None:
            return None
        task_id = str(task.get("id") or "").strip()
        if not task_id:
            logger.warning("活动委托缺少 taskId，跳过状态更新。eventId=%s", event.event_id)
            return None

        history = await self._load_delegated_task_history(event, history_context, task)
        task_state = self._delegated_task_state(task)

        try:
            # ReAct 图已经完成语义决策。普通发送成功后只将图状态落库，不能再调用
            # 旧运行图做第二次判断，否则会把“已确认/将结束”等状态重新覆盖成等待。
            if self._is_react_managed_delegated_action(delegated_action):
                if getattr(delegated_action, "action", "") == "SEND_AND_COMPLETE":
                    if self._qq_write_back_effectively_succeeded(write_back_actions):
                        return await self._persist_delegated_task_decision(
                            event=event,
                            task=task,
                            decision=delegated_action,
                            results=results,
                        )
                    return await self._persist_delegated_task_decision(
                        event=event,
                        task=task,
                        decision=self._build_react_post_send_wait_action(
                            event,
                            delegated_action,
                            write_back_actions=write_back_actions,
                        ),
                        results=results,
                    )

                if getattr(delegated_action, "action", "") == "SEND_MESSAGE":
                    return await self._persist_delegated_task_decision(
                        event=event,
                        task=task,
                        decision=self._build_react_post_send_wait_action(
                            event,
                            delegated_action,
                            write_back_actions=write_back_actions,
                        ),
                        results=results,
                    )

            if (
                getattr(delegated_action, "action", "") == "SEND_AND_COMPLETE"
                and self._qq_write_back_effectively_succeeded(write_back_actions)
            ):
                return await self._persist_delegated_task_decision(
                    event=event,
                    task=task,
                    decision=delegated_action,
                    results=results,
                )

            # SEND_MESSAGE 的动作状态尚未单独落库，发送成功后以它作为本轮恢复点，
            # 防止运行图从旧 stateJson 出发丢失已知事实、待满足条件和首发状态。
            task_for_writeback = {
                **task,
                "stateJson": getattr(delegated_action, "state_json", "") or task.get("stateJson") or "{}",
            }
            decision = await self.delegated_task_workflow.evaluate_runtime(
                DelegatedTaskRuntimeInput(
                    task=task_for_writeback,
                    history=history,
                    event=event.model_dump(by_alias=True),
                    finalReply=final_reply,
                    writeBackActions=write_back_actions,
                    preTaskHistory=list(task_state.get("preTaskHistory") or []),
                    historyAccessAllowed=self._delegated_task_history_access_allowed(task),
                ),
                model_profile,
            )
            return await self._persist_delegated_task_decision(
                event=event,
                task=task,
                decision=decision,
                results=results,
            )
        except DelegatedWorkflowCompletionError:
            raise
        except Exception as exception:
            logger.warning(
                "委托任务状态更新失败，保留数据库原状态：taskId=%s, eventId=%s, error=%s",
                task_id,
                event.event_id,
                type(exception).__name__,
            )
            return None

    @staticmethod
    def _is_react_managed_delegated_action(decision) -> bool:
        """判断动作是否由主控台 ReAct 图生成，而不是旧设定集运行时分支。"""
        arguments = getattr(decision, "tool_arguments", None)
        return isinstance(arguments, dict) and bool(arguments.get("reactManaged"))

    def _build_react_post_send_wait_action(
        self,
        event: UnifiedEvent,
        action: DelegatedTaskActionDecision,
        *,
        write_back_actions: list[str],
    ) -> DelegatedTaskActionDecision:
        """把 ReAct 的发送动作转换为可持久化的 ACTIVE 状态更新。

        ``send_qq_message`` 是副作用工具，不能直接作为状态写库工具。发送后保留原图
        的 workingMemory、证据和待满足条件，并只更新本轮已发送或发送失败的信息。
        """
        state = self._delegated_task_state({"stateJson": action.state_json})
        stable_event_id = self._stable_event_identity(event)
        sent = self._qq_write_back_effectively_succeeded(write_back_actions)
        duplicate_suppressed = any(
            str(item or "").startswith("qq_write_back_skipped:duplicate_outbound:")
            for item in write_back_actions
        )
        state["lastWriteBackEventId"] = stable_event_id
        state["lastWriteBackStatus"] = "DEDUPLICATED" if duplicate_suppressed else ("SENT" if sent else "FAILED")
        state["lastPlannedAction"] = "SEND_MESSAGE"
        state["lastPlannedAt"] = state.get("lastPlannedAt") or ""
        ledger = list(state.get("outboundLedger") or [])
        for record in self._outbound_write_back_records(write_back_actions):
            if any(
                isinstance(existing, dict)
                and existing.get("turnAnchor") == record["turnAnchor"]
                and existing.get("contentDigest") == record["contentDigest"]
                for existing in ledger
            ):
                continue
            ledger.append(record)
        state["outboundLedger"] = ledger[-80:]
        if sent:
            progress = action.progress_summary or "已发送消息，等待联系人回复"
        else:
            progress = "消息发送失败，等待下次事件或手动重试"
        return DelegatedTaskActionDecision(
            action="WAIT",
            reason=action.reason or "已记录 ReAct 发送结果",
            progressSummary=progress,
            stateJson=json.dumps(state, ensure_ascii=False),
            lastEventId=stable_event_id,
            completionReport="",
            evidence=list(action.evidence or []),
            requestedTool="update_delegated_task",
            toolArguments={
                **dict(action.tool_arguments or {}),
                "reactManaged": True,
                "writeBackSucceeded": sent,
            },
        )

    @staticmethod
    def _qq_write_back_succeeded(write_back_actions: list[str]) -> bool:
        """判断本轮消息是否已经由 QQ 连接器确认发送成功。

        只有 ``qq_write_back_sent`` 才能作为结束任务的提交条件；跳过、失败和未知状态
        都必须保留任务为 ACTIVE，避免出现“任务已完成但收尾消息没有发出去”的分裂状态。
        """
        for action in write_back_actions:
            action_text = str(action or "").strip()
            if not action_text.startswith("qq_write_back_sent:"):
                continue
            parts = action_text.split(":")
            status = parts[1].strip().lower() if len(parts) > 1 else ""
            return status not in {"", "unknown", "failed", "error"}
        return False

    @staticmethod
    def _outbound_write_back_records(write_back_actions: list[str]) -> list[dict]:
        """从发送结果中提取可持久化的轮次与内容摘要。"""
        records: list[dict] = []
        for action in write_back_actions:
            parts = str(action or "").split(":")
            if len(parts) >= 5 and parts[0] == "qq_write_back_sent":
                records.append(
                    {
                        "turnAnchor": parts[2],
                        "contentDigest": parts[3],
                        "sentAt": datetime.now(timezone.utc).isoformat(),
                        "triggerEventId": parts[4],
                    }
                )
                continue
            # 并发分支命中相同工具幂等键时，平台只会真实发送一次。命中方也要
            # 落下相同账本记录，避免后提交的状态覆盖先提交分支的发送记录。
            if len(parts) >= 4 and parts[:2] == ["qq_write_back_skipped", "duplicate_outbound"]:
                records.append(
                    {
                        "turnAnchor": parts[2],
                        "contentDigest": parts[3],
                        "sentAt": datetime.now(timezone.utc).isoformat(),
                        "triggerEventId": "",
                    }
                )
        return records

    @classmethod
    def _qq_write_back_effectively_succeeded(cls, write_back_actions: list[str]) -> bool:
        """真实发送成功或命中已发送幂等记录，都视为本轮副作用已经完成。"""
        if cls._qq_write_back_succeeded(write_back_actions):
            return True
        return any(
            str(action or "").startswith("qq_write_back_skipped:duplicate_outbound:")
            for action in write_back_actions
        )

    async def _evaluate_task_completion(
        self,
        event: UnifiedEvent,
        route: str,
        profile_match: ConversationProfileMatchResult | None,
        history_context: list[dict],
        final_reply: str,
        model_profile,
    ) -> dict | None:
        """在不影响主回复的前提下判断任务是否完成，并提交待用户审批的结束申请。"""
        if self.task_completion_service is None:
            return None
        try:
            return await self.task_completion_service.evaluate_and_request(
                event=event,
                route=route,
                profile_match=profile_match,
                history_context=history_context,
                final_reply=final_reply,
                model_profile=model_profile,
            )
        except Exception as exception:
            logger.warning(
                "提交会话任务结束申请失败，保留当前代理状态：eventId=%s, error=%s",
                event.event_id,
                type(exception).__name__,
            )
            return None

    @staticmethod
    def _collect_verified_memory_ids(verified_memories) -> list[str]:
        """提取本次实际注入的长期记忆 ID，并按原顺序去重以供执行轨迹审计。"""
        memory_ids: list[str] = []
        for memory in verified_memories:
            memory_id = str(memory.id).strip()
            if memory_id and memory_id not in memory_ids:
                memory_ids.append(memory_id)
        return memory_ids

    @staticmethod
    def _build_notification_decision(results: list[AgentResult]) -> NotificationDecision | None:
        """
        将收件箱分发 Agent 的内部结构化结果转换为工作台可长期依赖的通知决策。

        其他 Agent 不应自行决定桌面提醒等级，避免同一条消息在不同工作流中产生互相矛盾的优先级。
        """
        dispatch_result = next((result for result in results if result.agent == "inbox_dispatch"), None)
        if dispatch_result is None:
            return None

        decision = dispatch_result.structured_result
        dispatch_mode = str(decision.get("dispatchMode", "normal"))
        reason = str(decision.get("urgencyReason", "none"))
        flushed = bool(decision.get("flushed", False))
        notify_now = bool(decision.get("shouldNotifyNow", False))
        suppressed = bool(decision.get("suppressedByPolicy", False))

        if suppressed:
            priority = "NONE"
            aggregation_status = "SUPPRESSED"
        elif dispatch_mode == "urgent":
            priority = "HIGH"
            aggregation_status = "IMMEDIATE"
        elif flushed:
            priority = "NORMAL"
            aggregation_status = "SUMMARY_READY"
        else:
            priority = "LOW"
            aggregation_status = "BUFFERED"

        return NotificationDecision(
            channel=dispatch_mode,
            priority=priority,
            trigger_reason=reason,
            notify_now=notify_now,
            aggregation_key=str(decision.get("aggregationKey", "")),
            aggregation_status=aggregation_status,
            buffered_count=int(decision.get("bufferedCount", 0)),
            summary_candidate=str(decision.get("summaryCandidate") or ""),
        )

    async def _write_back_if_needed(
        self,
        event: UnifiedEvent,
        route: str,
        results: list[AgentResult],
        final_reply: str,
        profile_match: ConversationProfileMatchResult | None,
        delegated_task: dict | None = None,
    ) -> list[str]:
        # 这个函数的作用是判断当前结果是否需要回写原平台，并记录回写结果。
        if event.platform != "qq":
            return []

        payload = self._build_write_back_payload(
            event,
            route,
            results,
            final_reply,
            profile_match,
            delegated_task,
        )
        if payload is None:
            actions = self._build_write_back_skip_actions(profile_match, route, results, final_reply)
            logger.info("QQ 自动回写未执行：eventId=%s, route=%s, actions=%s", event.event_id, route, actions)
            return actions

        message_parts = list(payload.pop("message_parts", []))
        asset_ok, asset_actions = await self._send_approved_assets(
            event,
            results,
            profile_match,
        )
        if not asset_ok:
            # 资产发送失败时禁止继续发送可能包含“已经发你了”等承诺的文本。
            return asset_actions
        delay_seconds = self._resolve_reply_delay_seconds(event, profile_match)
        if delay_seconds > 0:
            await self.sleeper(delay_seconds)

        try:
            # 模型显式换行时可按多条消息发送；运行时不再按固定字符数切割正文。
            payloads = self._build_message_payloads(payload, message_parts)
            if self._is_delegated_write_back_superseded(event, delegated_task):
                logger.info(
                    "QQ 自动回写发送前发现委托回复已过期：taskId=%s, eventId=%s",
                    (delegated_task or {}).get("id"),
                    event.event_id,
                )
                return ["qq_write_back_skipped:delegated_superseded"]
            stable_event_id = self._stable_event_identity(event)
            turn_anchor = self._delegated_turn_anchor(event, delegated_task)
            if delegated_task is not None:
                # 主控台任务必须以会话而非 taskId 去重。多个并行任务面对同一联系人、
                # 同一轮对话和同一候选文本时，只允许一次真正的平台发送。
                task_scope = f"conversation:{self._conversation_idempotency_scope(event)}"
            else:
                # 设定集自动回复保持事件级作用域，允许后续不同时间的正常重复表达。
                task_scope = f"event:{stable_event_id}"
            responses = []
            send_actions: list[str] = []
            call_keys: list[str] = []
            for index, source_payload in enumerate(payloads):
                if self._is_delegated_write_back_superseded(event, delegated_task):
                    logger.info(
                        "QQ 自动回写分段发送中止：taskId=%s, eventId=%s, index=%s",
                        (delegated_task or {}).get("id"),
                        event.event_id,
                        index,
                    )
                    return ["qq_write_back_skipped:delegated_superseded"]
                message_payload = dict(source_payload)
                content_digest = self._outbound_payload_digest(message_payload)
                call_key = f"runtime:{task_scope}:turn:{turn_anchor}:content:{content_digest}"
                call_keys.append(call_key)
                if self._outbound_already_recorded(delegated_task, turn_anchor, content_digest):
                    logger.info(
                        "QQ 自动回写抑制重复内容：taskId=%s, turn=%s, digest=%s, index=%s",
                        (delegated_task or {}).get("id"),
                        turn_anchor,
                        content_digest,
                        index,
                    )
                    send_actions.append(
                        f"qq_write_back_skipped:duplicate_outbound:{turn_anchor}:{content_digest}"
                    )
                    continue
                message_payload["client_message_id"] = call_key
                message_payload["correlation_id"] = event.event_id
                # 所有平台发送均由 LangChain 工具执行；设定集和主控台仅在授权来源上不同。
                response = await self.tools.ainvoke(
                    "send_qq_message",
                    context=ToolExecutionContext(
                        user_id=self._resolve_event_user_id(event),
                        event_id=event.event_id,
                        task_id=str((delegated_task or {}).get("id") or ""),
                        allowed_tools=frozenset({"send_qq_message"}),
                    ),
                    idempotency_key=call_key,
                    arguments=message_payload,
                )
                responses.append(response)
                status = str(response.get("status", "unknown"))
                send_actions.append(
                    f"qq_write_back_sent:{status}:{turn_anchor}:{content_digest}:{stable_event_id}"
                )
                if index < len(payloads) - 1:
                    await self.sleeper(self._resolve_inter_bubble_delay_seconds(event, index))
            for result in results:
                if result.agent == "inbox_dispatch":
                    result.tool_calls.append(
                        ToolCallRecord(
                            tool="send_qq_message",
                            arguments={"messages": payloads, "idempotencyKeys": call_keys},
                        )
                    )
                    break
            actions = list(asset_actions)
            if delay_seconds > 0:
                actions.append(f"qq_write_back_delayed:{delay_seconds}s")
            if len(payloads) > 1:
                actions.append(f"qq_write_back_split:{len(payloads)}")
            actions.extend(send_actions)
            return actions
        except KeyError:
            return ["qq_write_back_skipped:tool_not_registered"]
        except Exception as exc:
            return [f"qq_write_back_failed:{exc}"]

    async def _send_approved_assets(
        self,
        event: UnifiedEvent,
        results: list[AgentResult],
        profile_match: ConversationProfileMatchResult | None,
    ) -> tuple[bool, list[str]]:
        """发送经 ReviewAgent 批准且属于当前 Profile 的资产，任何越权或失败都采用 fail-closed。"""
        review = next((result for result in results if result.agent == "review"), None)
        if review is None or review.structured_result.get("reviewDecision") != "APPROVE":
            return True, []
        requested_ids = [
            str(item).strip()
            for item in (review.structured_result.get("assetRequests") or [])
            if str(item).strip()
        ]
        if not requested_ids:
            return True, []
        if not profile_match or not profile_match.profile:
            return False, ["secure_asset_skipped:profile_unavailable"]

        allowed_ids = {
            reference.asset_id
            for reference in profile_match.profile.profile_context.assets
            if reference.asset_id
        }
        if any(asset_id not in allowed_ids for asset_id in requested_ids):
            return False, ["secure_asset_skipped:not_authorized_by_profile"]

        if "send_secure_asset" not in self.tools.names():
            return False, ["secure_asset_skipped:tool_not_registered"]

        user_id = EventCenterServiceClient.resolve_event_user_id(event)
        actions: list[str] = []
        try:
            for asset_id in requested_ids:
                response = await self.tools.ainvoke(
                    "send_secure_asset",
                    context=ToolExecutionContext(
                        user_id=user_id,
                        event_id=event.event_id,
                        allowed_tools=frozenset({"send_secure_asset"}),
                    ),
                    idempotency_key=f"secure-asset:{event.event_id}:{asset_id}",
                    arguments={
                        "asset_id": asset_id,
                        "user_id": user_id,
                        "chat_type": event.chat_type,
                        "chat_id": event.chat_id,
                        "allowed_asset_ids": sorted(allowed_ids),
                    },
                )
                actions.append(f"secure_asset_sent:{asset_id}:{response.get('status', 'unknown')}")
                review.tool_calls.append(
                    ToolCallRecord(
                        tool="send_secure_asset",
                        arguments={"assetId": asset_id, "chatType": event.chat_type, "chatId": event.chat_id},
                    )
                )
            return True, actions
        except Exception as exc:
            logger.warning(
                "安全资产发送失败：eventId=%s, assetIds=%s, error=%s",
                event.event_id,
                requested_ids,
                type(exc).__name__,
            )
            return False, [f"secure_asset_failed:{type(exc).__name__}"]

    def _build_write_back_payload(
        self,
        event: UnifiedEvent,
        route: str,
        results: list[AgentResult],
        final_reply: str,
        profile_match: ConversationProfileMatchResult | None,
        delegated_task: dict | None = None,
    ) -> dict[str, object] | None:
        # 这个函数的作用是按不同场景生成平台回写参数。
        # 代码级人工审批优先于设定集 AUTO_REPLY，任何 Agent 都不能绕过该闸门。
        if any(result.need_confirmation or result.structured_result.get("handoffRequired") for result in results):
            return None
        if route == "social_reply":
            review = next((result for result in results if result.agent == "review"), None)
            if review is None or review.structured_result.get("reviewDecision") != "APPROVE":
                return None
        # 主界面创建的活动委托本身就是当前精确会话的自动执行授权，不要求用户再建一份 AUTO_REPLY 设定。
        if delegated_task is None and not self._should_auto_write_back(profile_match):
            return None

        if route == "message_dispatch":
            # 快慢通道属于工作台通知，绝不将系统提醒或摘要回声发送到原始聊天。
            return None

        if event.chat_type == "private":
            if not final_reply or final_reply == "No reply was generated.":
                return None
            return {
                "chat_type": "private",
                "chat_id": event.chat_id,
                "message": final_reply,
                "message_parts": self._extract_social_message_parts(results, final_reply),
            }

        delegated_group = bool(
            delegated_task
            and str(delegated_task.get("platform") or "").lower() == event.platform.lower()
            and str(delegated_task.get("chatType") or "").lower() == event.chat_type.lower()
            and str(delegated_task.get("chatId") or "") == event.chat_id
        )
        if self._is_at_self(event) or delegated_group:
            if not final_reply or final_reply == "No reply was generated.":
                return None
            if delegated_group and not self._is_at_self(event):
                return {
                    "chat_type": "group",
                    "chat_id": event.chat_id,
                    "message": final_reply,
                }
            return {
                "chat_type": "group",
                "chat_id": event.chat_id,
                "segments": [
                    {"type": "at", "data": {"qq": event.sender.id}},
                    {"type": "text", "data": {"text": f" {final_reply}"}} ,
                ],
            }

        return None

    @staticmethod
    def _extract_social_message_parts(results: list[AgentResult], final_reply: str) -> list[str]:
        # 这个函数的作用是优先读取审批 Agent 最终通过的短气泡；未改写时再兼容 SocialAgent 原始分段。
        # 必须验证分段合并后等于 final_reply，防止把审查前旧草稿误发到 QQ。
        for agent_name in ("review", "social"):
            result = next((item for item in results if item.agent == agent_name), None)
            if result is None:
                continue
            raw_parts = result.structured_result.get("messageParts") or []
            if not isinstance(raw_parts, list):
                continue
            parts = [str(part).strip() for part in raw_parts if str(part).strip()]
            if parts and "\n".join(parts) == final_reply:
                return parts
        return []

    @staticmethod
    def _build_message_payloads(payload: dict[str, object], message_parts: list[str]) -> list[dict[str, object]]:
        # 这个函数的作用是把同一次自动回复转换为一条或多条可发送的 QQ 工具参数。
        # 没有合法分段时完全复用原 payload，确保日程、任务等非社交链路不受影响。
        if payload.get("chat_type") != "private" or len(message_parts) < 2:
            return [payload]

        return [
            {
                **payload,
                "message": message_part,
            }
            for message_part in message_parts
        ]

    @staticmethod
    def _resolve_inter_bubble_delay_seconds(event: UnifiedEvent, index: int) -> float:
        # 这个函数的作用是为连续气泡生成 0.6 到 1.2 秒的稳定短间隔，模拟自然打字节奏。
        # 同一事件重试会得到相同延迟，避免重试时出现不可复现的发送行为。
        digest = hashlib.sha256(f"{event.event_id}:{index}".encode("utf-8")).digest()
        return 0.6 + (digest[0] % 7) / 10

    async def _match_conversation_profile(
        self,
        event: UnifiedEvent,
        preliminary_route: str,
    ) -> ConversationProfileMatchResult | None:
        # 这个函数的作用是在正式执行 agent 前，按预判 route 命中最合适的设定集。
        if self.event_center_client is None:
            return None
        try:
            return await self.event_center_client.match_conversation_profile(event, preliminary_route)
        except Exception as exception:
            # 设定加载失败会触发 fail-closed，记录上下文用于定位认证、用户归属或网络问题。
            logger.warning(
                "会话设定匹配失败，已禁止自动回写。userId=%s, platform=%s, chatType=%s, chatId=%s, error=%s",
                self._resolve_event_user_id(event),
                event.platform,
                event.chat_type,
                event.chat_id,
                exception,
            )
            return None

    async def _resolve_user_model_profile(
        self,
        event: UnifiedEvent,
        route: str,
        profile_match: ConversationProfileMatchResult | None,
        use_conversation_binding: bool = True,
    ) -> UserModelProfileResolveResult | None:
        # 这个函数的作用是在执行具体 agent 前，按事件里的可信用户解析模型配置。
        # 视觉等专用 route 会跳过会话绑定，确保 route 定向模型能覆盖日常回复模型。
        if self.event_center_client is None:
            return None
        try:
            profile_id = ""
            if use_conversation_binding and profile_match and profile_match.profile and profile_match.profile.model_profile_id:
                profile_id = profile_match.profile.model_profile_id
            return await self.event_center_client.resolve_user_model_profile(
                route=route,
                user_id=self._resolve_event_user_id(event),
                profile_id=profile_id or None,
            )
        except Exception as exception:
            # 模型解析失败只影响智能生成，不允许把 API Key 等敏感配置写入日志。
            logger.warning(
                "用户模型解析失败。userId=%s, profileId=%s, error=%s",
                self._resolve_event_user_id(event),
                profile_id or "default",
                exception,
            )
            return None

    @staticmethod
    def _select_vision_model_profile(conversation_profile, route_profile):
        """
        选择本次图片理解实际使用的模型。

        设定集显式绑定的模型代表用户对当前会话的直接选择，不能被一个“全 route”的
        默认文本模型覆盖。只有模型配置明确声明支持 vision_analysis 时，专用视觉 route
        才拥有更高优先级；否则优先沿用会话模型，最后才回退到全局模型。
        """
        if route_profile is not None:
            supported_routes = {
                str(route).strip().lower()
                for route in (route_profile.supported_routes or [])
                if str(route).strip()
            }
            if "vision_analysis" in supported_routes:
                return route_profile
        if conversation_profile is not None:
            return conversation_profile
        return route_profile

    @staticmethod
    def _resolve_event_user_id(event: UnifiedEvent) -> str:
        # 这个函数的作用是优先读取 event-center 写入的用户 ID，旧平台事件再回退到运行时环境配置。
        raw_user_id = event.raw_payload.get("userId") if event.raw_payload else None
        user_id = str(raw_user_id or "").strip()
        return user_id or os.getenv("MEMO_ECHO_RUNTIME_USER_ID") or "default"

    def _resolve_skills(
        self,
        profile_match: ConversationProfileMatchResult | None,
        route: str,
    ) -> tuple[list[SkillDescriptor], list[str]]:
        # 这个函数的作用是把会话设定里的 skill 引用解析成本地描述符，供 Agent 构造提示词和工具策略。
        if not profile_match or not profile_match.profile:
            return [], []

        skill_references = list(profile_match.profile.skill_references or [])
        single_reference = (profile_match.profile.skill_reference or "").strip()
        if single_reference and single_reference not in skill_references:
            skill_references.append(single_reference)
        if not skill_references:
            return [], []

        return self.skill_resolver.resolve_references(skill_references, route=route)

    def _resolve_allowed_tools(
        self,
        profile_match: ConversationProfileMatchResult | None,
        resolved_skills: list[SkillDescriptor],
    ) -> list[str]:
        # 这个函数的作用是把会话设定与 skill 的工具策略合并成最终工具白名单。
        available_tools = self.tools.names()
        privileged_tools = {"manage_qq_group", "send_secure_asset"}
        # 委托任务状态工具只属于主控台 LangGraph。设定集即使启用了全部普通工具，
        # 也不能获得更新或结束主控台任务的能力，避免长期会话设定自行终止。
        # 旧设定中可能残留这些主控台工作流动作名。它们不是通用 ToolRegistry
        # 工具，设定集不得把它们视为可授权能力。
        reserved_workflow_action_names = {"update_delegated_task", "complete_delegated_task"}
        profile_tools = (
            set(profile_match.profile.allowed_tools)
            if profile_match and profile_match.profile and profile_match.profile.allowed_tools
            else set()
        )
        # 特权工具默认拒绝。设定集中的特权工具采用“增量授权”，不会因为只勾选一个
        # 群管理权限而误删发送消息等普通工具。旧版针对普通工具的白名单语义保持不变。
        allowed_tool_set = set(available_tools) - privileged_tools - reserved_workflow_action_names
        if profile_tools:
            # 旧版本可能已经把主控台委托工具写入设定集白名单。这里必须先剔除，
            # 既不能把它们重新授权给设定集，也不能让这些无效项清空正常聊天工具。
            regular_profile_tools = profile_tools - privileged_tools - reserved_workflow_action_names
            if regular_profile_tools:
                allowed_tool_set &= regular_profile_tools
            allowed_tool_set |= privileged_tools & profile_tools

        # 绑定资产本身就是对该专用工具的最小授权；工具内部仍会再次校验资产 ID 白名单。
        if (
            profile_match
            and profile_match.profile
            and profile_match.profile.profile_context.assets
        ):
            allowed_tool_set.add("send_secure_asset")

        skill_allow_policies = [
            set(skill.tool_policy.allow)
            for skill in resolved_skills
            if skill.tool_policy.allow
        ]
        for skill_allowed in skill_allow_policies:
            allowed_tool_set &= skill_allowed

        return [tool_name for tool_name in available_tools if tool_name in allowed_tool_set]

    @staticmethod
    def _needs_human_confirmation(profile_match: ConversationProfileMatchResult | None) -> bool:
        # 这个函数的作用是把设定集里的“需人工确认”规则折叠成统一执行模式。
        if not profile_match or not profile_match.profile:
            return False
        return profile_match.profile.require_human_confirmation

    @staticmethod
    def _should_auto_write_back(profile_match: ConversationProfileMatchResult | None) -> bool:
        # 这个函数的作用是依据设定集统一判断本次是否允许自动回写。
        if not profile_match or not profile_match.profile:
            # 设定查询失败时采用 fail-closed，避免认证或网络异常绕过 DRAFT_ONLY 后直接发送消息。
            return False

        profile = profile_match.profile
        if profile.require_human_confirmation:
            return False
        if profile.reply_mode in {"SILENT", "DRAFT_ONLY"}:
            return False
        if profile.reply_mode == "AUTO_REPLY":
            return profile_match.active
        return profile_match.active

    @staticmethod
    def _build_write_back_skip_actions(
        profile_match: ConversationProfileMatchResult | None,
        route: str = "",
        results: list[AgentResult] | None = None,
        final_reply: str = "",
    ) -> list[str]:
        # 这个函数的作用是把“不自动回写”的原因显式写入结果，便于前端展示当前策略命中情况。
        if not profile_match or not profile_match.profile:
            return ["qq_write_back_skipped:profile_unavailable"]

        # 会话级策略优先于本次 Agent 诊断：DRAFT_ONLY/SILENT 本来就不应产生自动发送。
        profile = profile_match.profile
        if profile.require_human_confirmation:
            return ["qq_write_back_skipped:confirm_required"]
        if profile.reply_mode == "DRAFT_ONLY":
            return ["qq_write_back_skipped:draft_only"]
        if profile.reply_mode == "SILENT":
            return ["qq_write_back_skipped:silent"]
        if not profile_match.active:
            return ["qq_write_back_skipped:profile_inactive"]

        # 先输出 Agent 审查结论，避免前端只能看到“未发送”而不知道阻断点。
        for result in results or []:
            if result.structured_result.get("handoffRequired"):
                return ["qq_write_back_skipped:review_handoff"]
            if result.need_confirmation:
                return ["qq_write_back_skipped:agent_confirmation_required"]

        if route == "social_reply":
            review = next((result for result in results or [] if result.agent == "review"), None)
            if review is None:
                return ["qq_write_back_skipped:review_missing"]
            if review.structured_result.get("reviewDecision") != "APPROVE":
                return ["qq_write_back_skipped:review_not_approved"]
        if not final_reply or final_reply == "No reply was generated.":
            return ["qq_write_back_skipped:empty_reply"]

        return []

    @staticmethod
    def _resolve_reply_delay_seconds(
        event: UnifiedEvent,
        profile_match: ConversationProfileMatchResult | None,
    ) -> int:
        # 这个函数的作用是根据会话设定生成稳定的伪随机延迟秒数，避免每次都完全固定。
        if not profile_match or not profile_match.profile:
            return 0

        profile = profile_match.profile
        min_delay = profile.reply_delay_seconds_min or 0
        max_delay = profile.reply_delay_seconds_max or 0
        if min_delay <= 0 and max_delay <= 0:
            return 0

        lower = max(0, min(min_delay, max_delay))
        upper = max(0, max(min_delay, max_delay))
        if upper <= 0:
            return 0
        if lower == upper:
            return lower

        span = upper - lower + 1
        digest = hashlib.sha256(event.event_id.encode("utf-8")).digest()
        offset = digest[0] % span
        return lower + offset

    @staticmethod
    def _is_at_self(event: UnifiedEvent) -> bool:
        # 这个函数的作用是判断当前消息是否明确 @ 到机器人自身。
        if not event.self_id:
            return False
        return event.self_id in event.mentions
