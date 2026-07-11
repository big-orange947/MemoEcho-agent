from __future__ import annotations

import asyncio
import hashlib
import os
from uuid import uuid4

from app.clients.connector_service import ConnectorServiceClient
from app.clients.event_center_service import EventCenterServiceClient
from app.clients.llm_service import LlmServiceClient
from app.clients.schedule_service import ScheduleServiceClient
from app.clients.task_service import TaskServiceClient
from app.memory.manager import MemoryManager
from app.orchestrator.registry import build_agent_registry
from app.planner.service import PlannerService
from app.router.service import RouterService
from app.schemas.events import UnifiedEvent
from app.schemas.model_profiles import UserModelProfileResolveResult
from app.schemas.profiles import ConversationProfileMatchResult
from app.schemas.results import AgentResult, NotificationDecision, OrchestratorResult, ToolCallRecord
from app.schemas.skills import SkillDescriptor
from app.schemas.tasks import AgentTaskContext
from app.services.slow_channel_buffer import SlowChannelBuffer
from app.skills.resolver import SkillResolver
from app.tools.create_schedule_tool import CreateScheduleTool
from app.tools.create_task_tool import CreateTaskTool
from app.tools.extract_file_text_tool import ExtractFileTextTool
from app.tools.get_recent_messages_tool import GetRecentMessagesTool
from app.tools.list_tasks_tool import ListTasksTool
from app.tools.registry import ToolRegistry
from app.tools.send_qq_message_tool import SendQqMessageTool


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
        sleeper=None,
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
        self.sleeper = sleeper or asyncio.sleep

    @classmethod
    def build_default(cls) -> "OrchestratorService":
        # 这个函数的作用是组装本地默认运行时依赖，方便直接启动整条链路。
        event_center_client = EventCenterServiceClient()
        llm_client = LlmServiceClient()
        tools = ToolRegistry()
        tools.register("extract_file_text", ExtractFileTextTool())
        tools.register("get_recent_messages", GetRecentMessagesTool(event_center_client))
        tools.register("create_schedule", CreateScheduleTool(ScheduleServiceClient()))
        tools.register("create_task", CreateTaskTool(TaskServiceClient()))
        tools.register("list_tasks", ListTasksTool(TaskServiceClient()))
        tools.register("send_qq_message", SendQqMessageTool(ConnectorServiceClient()))
        slow_channel_buffer = SlowChannelBuffer()
        service = cls(
            router=RouterService(),
            planner=PlannerService(),
            tools=tools,
            memory=MemoryManager(),
            slow_channel_buffer=slow_channel_buffer,
            event_center_client=event_center_client,
            llm_client=llm_client,
            skill_resolver=SkillResolver.build_default(),
        )
        slow_channel_buffer.set_flush_callback(service._publish_slow_channel_digest)
        return service

    async def _publish_slow_channel_digest(self, flush) -> None:
        """把后台慢通道定时器产出的摘要交给事件中心持久化，失败时仅记录为后台任务失败。"""
        if self.event_center_client is None:
            return
        try:
            await self.event_center_client.publish_slow_channel_digest(flush)
        except Exception:
            return

    async def handle_event(self, event: UnifiedEvent) -> OrchestratorResult:
        # 这个函数的作用是驱动单次事件从粗路由、设定命中、执行到回写的完整主流程。
        execution_id = str(uuid4())
        preliminary_route = self.router.route(event)
        profile_match = await self._match_conversation_profile(event, preliminary_route)
        route = self.router.route(event, profile_match)
        resolved_skills, unresolved_skill_references = self._resolve_skills(profile_match, route)
        resolved_model_profile = await self._resolve_user_model_profile(event, route, profile_match)
        plan = self.planner.build_plan(route)

        base_context = AgentTaskContext(
            task_id=execution_id,
            route=route,
            event=event,
            history_context=self.memory.build_history_context(event),
            retrieved_knowledge=self.memory.build_retrieved_knowledge(event),
            allowed_tools=self._resolve_allowed_tools(profile_match, resolved_skills),
            execution_mode="confirm_required" if self._needs_human_confirmation(profile_match) else "suggest_only",
            metadata={
                "conversation_profile_match": profile_match.model_dump(by_alias=True) if profile_match else None,
                "resolved_model_profile": resolved_model_profile.model_dump(by_alias=True) if resolved_model_profile else None,
                "resolved_skills": [skill.model_dump(by_alias=True) for skill in resolved_skills],
                "unresolved_skill_references": unresolved_skill_references,
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

        final_reply = "\n".join(result.reply_draft for result in results if result.reply_draft).strip()
        if not final_reply:
            final_reply = "No reply was generated."

        write_back_actions = await self._write_back_if_needed(
            event,
            route,
            results,
            final_reply,
            profile_match,
        )
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
        )

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
    ) -> list[str]:
        # 这个函数的作用是判断当前结果是否需要回写原平台，并记录回写结果。
        if event.platform != "qq":
            return []

        payload = self._build_write_back_payload(event, route, results, final_reply, profile_match)
        if payload is None:
            return self._build_write_back_skip_actions(profile_match)

        delay_seconds = self._resolve_reply_delay_seconds(event, profile_match)
        if delay_seconds > 0:
            await self.sleeper(delay_seconds)

        try:
            send_tool = self.tools.get("send_qq_message")
            response = await send_tool.execute(**payload)
            for result in results:
                if result.agent == "inbox_dispatch":
                    result.tool_calls.append(
                        ToolCallRecord(
                            tool="send_qq_message",
                            arguments=payload,
                        )
                    )
                    break
            actions = []
            if delay_seconds > 0:
                actions.append(f"qq_write_back_delayed:{delay_seconds}s")
            actions.append(f"qq_write_back_sent:{response.get('status', 'unknown')}")
            return actions
        except KeyError:
            return ["qq_write_back_skipped:tool_not_registered"]
        except Exception as exc:
            return [f"qq_write_back_failed:{exc}"]

    def _build_write_back_payload(
        self,
        event: UnifiedEvent,
        route: str,
        results: list[AgentResult],
        final_reply: str,
        profile_match: ConversationProfileMatchResult | None,
    ) -> dict[str, object] | None:
        # 这个函数的作用是按不同场景生成平台回写参数。
        if not self._should_auto_write_back(profile_match):
            return None

        if route == "message_dispatch":
            dispatch_result = next((result for result in results if result.agent == "inbox_dispatch"), None)
            if dispatch_result is None:
                return None
            if not dispatch_result.structured_result.get("shouldNotifyNow"):
                return None
            if not dispatch_result.reply_draft:
                return None
            return {
                "chat_type": event.chat_type,
                "chat_id": event.chat_id,
                "message": dispatch_result.reply_draft,
            }

        if event.chat_type == "private":
            if not final_reply or final_reply == "No reply was generated.":
                return None
            return {
                "chat_type": "private",
                "chat_id": event.chat_id,
                "message": final_reply,
            }

        if self._is_at_self(event):
            if not final_reply or final_reply == "No reply was generated.":
                return None
            return {
                "chat_type": "group",
                "chat_id": event.chat_id,
                "segments": [
                    {"type": "at", "data": {"qq": event.sender.id}},
                    {"type": "text", "data": {"text": f" {final_reply}"}} ,
                ],
            }

        return None

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
        except Exception:
            return None

    async def _resolve_user_model_profile(
        self,
        event: UnifiedEvent,
        route: str,
        profile_match: ConversationProfileMatchResult | None,
    ) -> UserModelProfileResolveResult | None:
        # 这个函数的作用是在执行具体 agent 前，按事件里的可信用户解析模型配置，支持多用户桌面客户端。
        if self.event_center_client is None:
            return None
        try:
            profile_id = ""
            if profile_match and profile_match.profile and profile_match.profile.model_profile_id:
                profile_id = profile_match.profile.model_profile_id
            return await self.event_center_client.resolve_user_model_profile(
                route=route,
                user_id=self._resolve_event_user_id(event),
                profile_id=profile_id or None,
            )
        except Exception:
            return None

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
        allowed_tool_set = set(available_tools)

        if profile_match and profile_match.profile and profile_match.profile.allowed_tools:
            allowed_tool_set &= set(profile_match.profile.allowed_tools)

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
            return True

        profile = profile_match.profile
        if profile.require_human_confirmation:
            return False
        if profile.reply_mode in {"SILENT", "DRAFT_ONLY"}:
            return False
        if profile.reply_mode == "AUTO_REPLY":
            return profile_match.active
        return profile_match.active

    @staticmethod
    def _build_write_back_skip_actions(profile_match: ConversationProfileMatchResult | None) -> list[str]:
        # 这个函数的作用是把“不自动回写”的原因显式写入结果，便于前端展示当前策略命中情况。
        if not profile_match or not profile_match.profile:
            return []

        profile = profile_match.profile
        if profile.require_human_confirmation:
            return ["qq_write_back_skipped:confirm_required"]
        if profile.reply_mode == "DRAFT_ONLY":
            return ["qq_write_back_skipped:draft_only"]
        if profile.reply_mode == "SILENT":
            return ["qq_write_back_skipped:silent"]
        if not profile_match.active:
            return ["qq_write_back_skipped:profile_inactive"]
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
