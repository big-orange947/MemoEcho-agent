from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
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
from app.schemas.model_profiles import UserModelProfileResolveResult
from app.schemas.profiles import ConversationProfileMatchResult
from app.schemas.results import AgentResult, NotificationDecision, OrchestratorResult, ToolCallRecord
from app.schemas.schedules import SemanticIntentDecision
from app.schemas.skills import SkillDescriptor
from app.schemas.tasks import AgentTaskContext
from app.services.slow_channel_buffer import SlowChannelBuffer
from app.services.conversation_state_service import ConversationStateService
from app.services.conversation_task_completion import ConversationTaskCompletionService
from app.services.media_analysis_service import MediaAnalysisService
from app.services.schedule_intent_classifier import SemanticScheduleIntentClassifier
from app.skills.resolver import SkillResolver
from app.tools.extract_file_text_tool import ExtractFileTextTool
from app.tools.langchain_runtime_tools import build_runtime_tools, runtime_tool_specs
from app.tools.registry import ToolRegistry
from app.tools.send_secure_asset_tool import SendSecureAssetTool
from app.tools.base import ToolExecutionContext
from app.tools.qq_group_operations_tool import ManageQqGroupTool, QueryQqGroupTool
from app.workflows.delegated_task_graph import DelegatedTaskWorkflow


logger = logging.getLogger(__name__)


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
        self._delegated_inbound_versions: dict[str, int] = {}
        # 防抖窗口结束后，模型生成和平台回写仍可能继续数秒。保留最近事件 ID，
        # 让旧事件在真正发送前再做一次失效检查，而不是仅依赖 450ms 防抖。
        self._delegated_inbound_latest_event_ids: dict[str, str] = {}
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

    async def _handle_desktop_workspace_command(self, event: UnifiedEvent) -> OrchestratorResult:
        """处理主控台命令：读取联系人白名单、交给 LangGraph 编译任务，并在目标明确时主动启动。"""
        execution_id = f"desktop-command:{uuid4()}"
        user_id = EventCenterServiceClient.resolve_event_user_id(event)
        command = (event.text or "").strip()
        if not command:
            return OrchestratorResult(
                execution_id=execution_id,
                status="failed",
                route="delegated_task",
                summary="主控台命令为空",
            )
        if self.event_center_client is None:
            return OrchestratorResult(
                execution_id=execution_id,
                status="failed",
                route="delegated_task",
                summary="Event Center 未配置，无法创建委托任务",
            )

        try:
            candidates = await self.event_center_client.list_delegated_task_candidates(user_id)
        except Exception as exception:
            logger.warning("读取主控台委托候选会话失败：userId=%s, error=%s", user_id, exception)
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
            )

        if not candidates:
            return OrchestratorResult(
                execution_id=execution_id,
                status="failed",
                route="delegated_task",
                summary="没有可用联系人候选，请先完成 QQ 连接和联系人同步",
            )

        model_result = await self._safe_resolve_workspace_command_model(user_id)
        model_profile = model_result.profile if model_result else None
        # 主控台命令的目标解析统一交给 Python Runtime 的路由器。
        # Java 侧只负责提供授权候选，避免本地正则把“km预约”等动作词误拼进联系人名称。
        target_candidates = await self.delegated_task_workflow.resolve_workspace_command_targets(
            command=command,
            candidates=candidates,
            model_profile=model_profile,
        )
        candidate_batches = [[candidate] for candidate in target_candidates] if target_candidates else [candidates]
        target_resolved_by_router = bool(target_candidates)
        created_tasks: list[dict[str, Any]] = []
        results: list[AgentResult] = []

        for index, batch in enumerate(candidate_batches):
            try:
                compile_request = DelegatedTaskCompileRequest(
                    userId=user_id,
                    command=command,
                    conversations=batch,
                    targetResolvedByRouter=target_resolved_by_router,
                )
                compilation = await self.delegated_task_workflow.compile_task(compile_request, model_profile)
                if not compilation.recognized:
                    results.append(
                        AgentResult(
                            task_id=f"{execution_id}:compile:{index}",
                            agent="delegated_task_router",
                            status="needs_clarification",
                            structured_result=compilation.model_dump(by_alias=True),
                            reply_draft=compilation.clarification_question or "没有识别到明确的委托任务",
                            need_confirmation=True,
                        )
                    )
                    continue

                task = await self.event_center_client.create_delegated_task(user_id, command, compilation)
                created_tasks.append(task)
                task_id = str(task.get("id") or f"{execution_id}:task:{index}")
                task_status = str(task.get("status") or "CREATED")
                results.append(
                    AgentResult(
                        task_id=task_id,
                        agent="delegated_task_router",
                        status=task_status.lower(),
                        structured_result={
                            "task": task,
                            "compilation": compilation.model_dump(by_alias=True),
                        },
                        reply_draft=str(task.get("initialProgress") or "委托任务已创建"),
                        tool_calls=[
                            ToolCallRecord(
                                tool="create_delegated_task",
                                arguments={
                                    "chatId": task.get("chatId"),
                                    "chatType": task.get("chatType"),
                                    "targetName": task.get("targetName"),
                                },
                            )
                        ],
                        next_actions=["目标明确时 Runtime 会主动发起第一轮对话"],
                        need_confirmation=task_status.upper() == "WAITING_TARGET",
                    )
                )
                if task_status.upper() == "ACTIVE":
                    self._trigger_delegated_task_start(user_id, task)
            except Exception as exception:
                logger.exception("主控台委托任务创建失败：userId=%s, command=%s", user_id, command)
                results.append(
                    AgentResult(
                        task_id=f"{execution_id}:failed:{index}",
                        agent="delegated_task_router",
                        status="failed",
                        reply_draft=str(exception),
                        need_confirmation=True,
                    )
                )

        return OrchestratorResult(
            execution_id=execution_id,
            status="success" if created_tasks else "failed",
            route="delegated_task",
            summary=f"已创建 {len(created_tasks)} 个委托任务" if created_tasks else "未能创建委托任务",
            results=results,
            write_back_actions=[f"delegated_task_created:{len(created_tasks)}"] if created_tasks else [],
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

    def _build_workspace_command_candidate_batches(
        self,
        command: str,
        candidates: list[ConversationCandidate],
    ) -> list[list[ConversationCandidate]]:
        """按命令里显式提到的联系人拆分候选；命中私聊时优先私聊，避免误选同名群聊。"""
        matched_candidates = self._find_workspace_command_targets(command, candidates)
        if not matched_candidates:
            return [candidates]
        return [[candidate] for candidate in matched_candidates]

    def _find_workspace_command_targets(
        self,
        command: str,
        candidates: list[ConversationCandidate],
    ) -> list[ConversationCandidate]:
        """从联系人白名单里找出命令显式提到的目标，支持昵称、备注、QQ 号等直接命中。"""
        normalized_command = self._normalize_contact_token(command)
        scored: list[tuple[int, ConversationCandidate]] = []
        for candidate in candidates:
            score = 0
            for alias in self._candidate_aliases(candidate):
                if alias in normalized_command:
                    score = max(score, len(alias))
            if score <= 0:
                continue
            if candidate.chat_type == "private":
                score += 1000
            scored.append((score, candidate))

        private_scored = [(score, candidate) for score, candidate in scored if candidate.chat_type == "private"]
        selected = private_scored or scored
        selected.sort(key=lambda item: (-item[0], item[1].chat_name or item[1].chat_id))
        result: list[ConversationCandidate] = []
        seen: set[tuple[str, str, str]] = set()
        for _, candidate in selected:
            key = (candidate.platform, candidate.chat_type, candidate.chat_id)
            if key in seen:
                continue
            seen.add(key)
            result.append(candidate)
        return result

    def _candidate_aliases(self, candidate: ConversationCandidate) -> set[str]:
        """整理一个候选会话可被自然语言命中的别名，保持保守匹配以降低误选群聊概率。"""
        aliases: set[str] = set()
        for raw_value in (candidate.chat_name, candidate.last_sender_name, candidate.chat_id):
            alias = self._normalize_contact_token(raw_value)
            if alias and (len(alias) >= 2 or alias.isdigit()):
                aliases.add(alias)
        return aliases

    @staticmethod
    def _normalize_contact_token(value: str | None) -> str:
        """把昵称、备注和命令文本归一化，便于做轻量级显式提及匹配。"""
        if not value:
            return ""
        ignored_chars = set(" \t\r\n，,。.!！?？、:：;；@")
        return "".join(char.lower() for char in str(value).strip() if char not in ignored_chars)

    def _trigger_delegated_task_start(self, user_id: str, task: dict[str, Any]) -> None:
        """目标明确后异步投递一个内部启动事件，让委托任务运行图主动发起第一轮对话。"""
        task_id = str(task.get("id") or "").strip()
        chat_id = str(task.get("chatId") or "").strip()
        if not task_id or not chat_id:
            return
        now = datetime.now(timezone.utc).isoformat()
        start_event = UnifiedEvent(
            eventId=f"runtime:delegated-start:{task_id}:{uuid4()}",
            platform=str(task.get("platform") or "qq"),
            scene="delegated_task",
            eventType="delegated_task_started",
            chatType=str(task.get("chatType") or "private"),
            chatId=chat_id,
            selfId=str(task.get("accountId") or ""),
            sender=Sender(id=user_id, name="任务发起人", role="owner"),
            text="",
            attachments=[],
            mentions=[],
            segments=[],
            timestamp=now,
            rawPayload={
                "source": "python-runtime",
                "userId": user_id,
                "requestedRoute": "social_reply",
                "delegatedTaskId": task_id,
                "controlEvent": True,
            },
            actorType="SYSTEM",
            platformMessageId="",
            clientMessageId=f"runtime:delegated-start:{task_id}",
            correlationId=task_id,
            sequence=None,
            sentAt=now,
            receivedAt=now,
            importedAt=None,
            direction="INTERNAL",
            delegatedTaskId=task_id,
        )
        asyncio.create_task(self._run_delegated_task_start_event(start_event))

    async def _run_delegated_task_start_event(self, event: UnifiedEvent) -> None:
        """后台执行内部启动事件，失败时只记录日志，不阻塞主控台命令响应。"""
        try:
            await self.handle_event(event)
        except Exception:
            logger.exception("委托任务启动事件执行失败：eventId=%s, taskId=%s", event.event_id, event.delegated_task_id)

    async def handle_event(self, event: UnifiedEvent) -> OrchestratorResult:
        # 这个函数的作用是驱动单次事件从粗路由、设定命中、执行到回写的完整主流程。
        if self._is_desktop_workspace_command(event):
            return await self._handle_desktop_workspace_command(event)
        delegated_task = await self._get_active_delegated_task(event)
        execution_id = str(delegated_task.get("id") or uuid4()) if delegated_task else str(uuid4())
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
        plan = self.planner.build_plan(route)
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
                and not await self._wait_for_latest_delegated_inbound(event, delegated_task)
            ):
                delegated_action = self._build_superseded_delegated_wait_action(event, delegated_task)
            else:
                delegated_action = await self._decide_delegated_task_action(
                    event=event,
                    task=delegated_task,
                    history_context=history_context,
                    model_profile=resolved_model_profile,
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
                action_name = delegated_action.action.lower()
                write_back_actions = [f"delegated_task_action:{action_name}"]
                if persisted:
                    write_back_actions.append(f"delegated_task_runtime_updated:{action_name}")
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
        for step in plan.steps:
            # 这个循环的作用是按 planner 生成的步骤顺序逐个执行 agent，并把前序结果回灌给后续步骤。
            agent = self.agents[step.agent]
            step_context = base_context.model_copy(
                update={
                    "metadata": {
                        **base_context.metadata,
                        "previous_results": previous_results,
                    }
                }
            )
            result = await agent.run(step_context, step.action)
            results.append(result)
            previous_results[step.agent] = result.structured_result

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

        write_back_actions = await self._write_back_if_needed(
            event,
            route,
            results,
            final_reply,
            profile_match,
            delegated_task,
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

        return OrchestratorResult(
            execution_id=execution_id,
            status="success",
            route=route,
            summary=f"Plan executed in {plan.mode} mode with {len(plan.steps)} step(s).",
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

    async def _load_delegated_task_history(
        self,
        event: UnifiedEvent,
        fallback_history: list[dict],
        task: dict,
    ) -> list[dict]:
        """读取委托任务的可信双方时间线；服务异常时保留内存历史继续执行。"""
        history = list(fallback_history)
        task_id = str(task.get("id") or "").strip()
        task_state = self._delegated_task_state(task)
        task_created_at = str(
            task_state.get("taskCreatedAt") or task.get("createdAt") or ""
        ).strip()
        if self.event_center_client is None:
            return history
        try:
            return await self.event_center_client.list_conversation_messages(
                event.chat_id,
                platform=event.platform,
                chat_type=event.chat_type,
                # 任务内时间线从创建时刻开始；长期事实由 stateJson 的滚动记忆承载。
                limit=500,
                user_id=self._resolve_event_user_id(event),
                after=task_created_at or None,
            )
        except Exception as exception:
            logger.warning(
                "读取委托历史失败，使用当前内存上下文继续决策：taskId=%s, error=%s",
                task_id,
                type(exception).__name__,
            )
            return history

    async def _load_delegated_task_pre_history(
        self,
        event: UnifiedEvent,
        task: dict,
    ) -> list[dict]:
        """按需读取任务前少量消息，只作为背景，不能作为任务完成证据。"""
        if self.event_center_client is None or not self._delegated_task_history_access_allowed(task):
            return []
        task_state = self._delegated_task_state(task)
        task_created_at = str(
            task_state.get("taskCreatedAt") or task.get("createdAt") or ""
        ).strip()
        if not task_created_at:
            return []
        try:
            return await self.event_center_client.list_conversation_messages(
                event.chat_id,
                platform=event.platform,
                chat_type=event.chat_type,
                limit=30,
                user_id=self._resolve_event_user_id(event),
                before=task_created_at,
            )
        except Exception as exception:
            logger.warning(
                "读取任务前背景失败，继续使用任务内记忆：taskId=%s, error=%s",
                task.get("id"),
                type(exception).__name__,
            )
            return []

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
        """在 SocialAgent 运行前恢复背景并决定发送、等待或结束，失败时采用安全的事件方向回退。"""
        task_id = str(task.get("id") or "").strip()
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
                ),
                model_profile,
            )
            # 只有模型显式请求、用户已授权且尚未缓存时，才读取任务创建前的有限背景。
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
                    ),
                    model_profile,
                )
            return decision
        except Exception as exception:
            logger.warning(
                "委托动作决策失败，按当前事件方向保守回退：taskId=%s, eventId=%s, error=%s",
                task_id,
                event.event_id,
                type(exception).__name__,
            )
            # 决策图异常时，任务创建事件和联系人入站消息仍可继续，其他事件禁止触发自动回复。
            event_type = str(event.event_type or "").lower()
            if event_type == "delegated_task_started" or self._is_delegated_peer_inbound(event):
                return DelegatedTaskActionDecision(
                    action="SEND_MESSAGE",
                    reason="决策图不可用，按已授权任务和真实入站事件继续执行",
                    messageInstruction="根据委托目标和可信历史生成下一条消息",
                    stateJson=str(task.get("stateJson") or "{}"),
                    lastEventId=event.event_id,
                    requestedTool="send_qq_message",
                )
            # 非联系人入站事件必须显式返回 WAIT。返回 None 会让旧回复链继续运行，
            # 导致代理自己的出站回执、内部状态事件再次触发自动回复。
            return DelegatedTaskActionDecision(
                action="WAIT",
                reason="决策图不可用，当前事件不具备继续对话的可信触发条件",
                progressSummary=str(task.get("progressSummary") or "等待联系人回复"),
                stateJson=str(task.get("stateJson") or "{}"),
                lastEventId=event.event_id,
                requestedTool="update_delegated_task",
            )

    @staticmethod
    def _is_delegated_peer_inbound(event: UnifiedEvent) -> bool:
        """在任务图异常回退时识别真实联系人消息，规则与任务图保持一致。

        旧版 NapCat 事件可能没有 direction，但 ``message``、外部来源且没有自身参与者
        标记时仍应继续委托；明确的出站或 Agent 回显始终不能触发下一轮回复。
        """
        event_type = str(event.event_type or "").lower()
        direction = str(event.direction or "").upper()
        actor = str(event.actor_type or "").upper()
        raw_payload = event.raw_payload or {}
        origin = str(raw_payload.get("messageOrigin") or "").upper()
        if not (event.text or "").strip() or event_type != "message":
            return False
        if direction == "OUTBOUND" or actor in {"OWNER", "AGENT", "SYSTEM"}:
            return False
        if origin in {
            "INTERNAL",
            "AGENT",
            "AGENT_AUTO",
            "AGENT_CONFIRMED",
            "USER_MANUAL",
        }:
            return False
        if direction == "INBOUND" or actor == "CONTACT":
            return True
        return origin in {"EXTERNAL", "PLATFORM"}

    async def _wait_for_latest_delegated_inbound(
        self,
        event: UnifiedEvent,
        task: dict,
    ) -> bool:
        """等待短暂合并窗口，只有同一任务内最新的联系人消息才继续生成回复。"""
        task_id = str(task.get("id") or "").strip()
        if not task_id:
            return True

        version = self._delegated_inbound_versions.get(task_id, 0) + 1
        self._delegated_inbound_versions[task_id] = version
        self._delegated_inbound_latest_event_ids[task_id] = event.event_id
        await self.sleeper(self._delegated_inbound_debounce_seconds)
        is_latest = self._delegated_inbound_versions.get(task_id) == version
        return is_latest

    def _is_latest_delegated_inbound(self, event: UnifiedEvent, task: dict) -> bool:
        """确认当前事件仍是该委托会话最新的联系人入站消息。

        函数在真正回写 QQ 前调用。它只保护同一常驻 Python 进程中的并发事件；
        服务重启后由事件幂等键和任务时间线负责避免重复执行。
        """
        task_id = str(task.get("id") or "").strip()
        if not task_id:
            return True
        latest_event_id = self._delegated_inbound_latest_event_ids.get(task_id)
        return not latest_event_id or latest_event_id == event.event_id

    def _is_delegated_write_back_superseded(self, event: UnifiedEvent, task: dict | None) -> bool:
        """判断委托任务回写是否已经被同一会话里的更新联系人消息覆盖。"""
        if not task:
            return False
        return self._is_delegated_peer_inbound(event) and not self._is_latest_delegated_inbound(event, task)

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
            lastEventId=event.event_id,
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
        tool_context = ToolExecutionContext(
            user_id=self._resolve_event_user_id(event),
            event_id=event.event_id,
            task_id=task_id,
            allowed_tools=frozenset({"update_delegated_task", "complete_delegated_task"}),
        )
        idempotency_key = f"delegated:{task_id}:{decision.last_event_id or event.event_id}:{requested_tool}"
        try:
            response = await self.tools.ainvoke(
                requested_tool,
                context=tool_context,
                idempotency_key=idempotency_key,
                arguments={
                    "event": event.model_dump(mode="json"),
                    "task_id": task_id,
                    "progress_summary": decision.progress_summary,
                    "state_json": decision.state_json,
                    "last_event_id": decision.last_event_id or event.event_id,
                    "completion_report": decision.completion_report,
                },
            )
            if results:
                results[-1].tool_calls.append(
                    ToolCallRecord(
                        tool=requested_tool,
                        arguments={
                            "taskId": task_id,
                            "action": getattr(decision, "action", "RUNTIME_UPDATE"),
                            "evidence": decision.evidence,
                            "idempotencyKey": idempotency_key,
                        },
                    )
                )
            return response
        except Exception as exception:
            logger.warning(
                "委托任务状态持久化失败，保留数据库原状态：taskId=%s, eventId=%s, error=%s",
                task_id,
                event.event_id,
                type(exception).__name__,
            )
            return None

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
                    if self._qq_write_back_succeeded(write_back_actions):
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
                            sent=False,
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
                            sent=self._qq_write_back_succeeded(write_back_actions),
                        ),
                        results=results,
                    )

            if (
                getattr(delegated_action, "action", "") == "SEND_AND_COMPLETE"
                and self._qq_write_back_succeeded(write_back_actions)
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
        sent: bool,
    ) -> DelegatedTaskActionDecision:
        """把 ReAct 的发送动作转换为可持久化的 ACTIVE 状态更新。

        ``send_qq_message`` 是副作用工具，不能直接作为状态写库工具。发送后保留原图
        的 workingMemory、证据和待满足条件，并只更新本轮已发送或发送失败的信息。
        """
        state = self._delegated_task_state({"stateJson": action.state_json})
        state["lastWriteBackEventId"] = event.event_id
        state["lastWriteBackStatus"] = "SENT" if sent else "FAILED"
        state["lastPlannedAction"] = "SEND_MESSAGE"
        state["lastPlannedAt"] = state.get("lastPlannedAt") or ""
        if sent:
            progress = action.progress_summary or "已发送消息，等待联系人回复"
        else:
            progress = "消息发送失败，等待下次事件或手动重试"
        return DelegatedTaskActionDecision(
            action="WAIT",
            reason=action.reason or "已记录 ReAct 发送结果",
            progressSummary=progress,
            stateJson=json.dumps(state, ensure_ascii=False),
            lastEventId=action.last_event_id or event.event_id,
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
            status = action_text.split(":", 1)[1].strip().lower()
            return status not in {"", "unknown", "failed", "error"}
        return False

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
            task_scope = str((delegated_task or {}).get("id") or route)
            idempotency_key = f"runtime:{event.event_id}:{task_scope}"
            responses = []
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
                call_key = f"{idempotency_key}:message:{index}"
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
                if index < len(payloads) - 1:
                    await self.sleeper(self._resolve_inter_bubble_delay_seconds(event, index))
            for result in results:
                if result.agent == "inbox_dispatch":
                    result.tool_calls.append(
                        ToolCallRecord(
                            tool="send_qq_message",
                            arguments={"messages": payloads, "idempotencyKey": idempotency_key},
                        )
                    )
                    break
            actions = list(asset_actions)
            if delay_seconds > 0:
                actions.append(f"qq_write_back_delayed:{delay_seconds}s")
            if len(payloads) > 1:
                actions.append(f"qq_write_back_split:{len(payloads)}")
            status = responses[-1].get("status", "unknown") if responses else "unknown"
            actions.append(f"qq_write_back_sent:{status}")
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
