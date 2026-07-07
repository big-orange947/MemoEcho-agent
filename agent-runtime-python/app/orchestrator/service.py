from __future__ import annotations

from uuid import uuid4

from app.clients.connector_service import ConnectorServiceClient
from app.clients.event_center_service import EventCenterServiceClient
from app.clients.schedule_service import ScheduleServiceClient
from app.clients.task_service import TaskServiceClient
from app.memory.manager import MemoryManager
from app.orchestrator.registry import build_agent_registry
from app.planner.service import PlannerService
from app.router.service import RouterService
from app.schemas.events import UnifiedEvent
from app.schemas.results import AgentResult, OrchestratorResult, ToolCallRecord
from app.schemas.tasks import AgentTaskContext
from app.services.slow_channel_buffer import SlowChannelBuffer
from app.tools.create_schedule_tool import CreateScheduleTool
from app.tools.create_task_tool import CreateTaskTool
from app.tools.get_recent_messages_tool import GetRecentMessagesTool
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
    ) -> None:
        # 这个构造函数的作用是保存运行时依赖，并在启动阶段一次性构建 agent 注册表。
        self.router = router
        self.planner = planner
        self.tools = tools
        self.memory = memory
        self.agents = build_agent_registry(tools, slow_channel_buffer)

    @classmethod
    def build_default(cls) -> "OrchestratorService":
        # 这个函数的作用是组装本地默认运行时依赖，方便直接启动整条链路。
        tools = ToolRegistry()
        tools.register("get_recent_messages", GetRecentMessagesTool(EventCenterServiceClient()))
        tools.register("create_schedule", CreateScheduleTool(ScheduleServiceClient()))
        tools.register("create_task", CreateTaskTool(TaskServiceClient()))
        tools.register("send_qq_message", SendQqMessageTool(ConnectorServiceClient()))
        slow_channel_buffer = SlowChannelBuffer()
        return cls(
            router=RouterService(),
            planner=PlannerService(),
            tools=tools,
            memory=MemoryManager(),
            slow_channel_buffer=slow_channel_buffer,
        )

    async def handle_event(self, event: UnifiedEvent) -> OrchestratorResult:
        # 这个函数的作用是驱动单次事件从路由、规划、执行到回写的完整主流程。
        execution_id = str(uuid4())
        route = self.router.route(event)
        plan = self.planner.build_plan(route)

        # 这里先准备所有 agent 共用的执行上下文，避免每个 agent 重复拼装输入。
        base_context = AgentTaskContext(
            task_id=execution_id,
            route=route,
            event=event,
            history_context=self.memory.build_history_context(event),
            retrieved_knowledge=self.memory.build_retrieved_knowledge(event),
            allowed_tools=self.tools.names(),
        )

        results: list[AgentResult] = []
        for step in plan.steps:
            # planner 只决定“谁做什么”，真正执行从这里按步骤串起来。
            agent = self.agents[step.agent]
            result = await agent.run(base_context, step.action)
            results.append(result)

        # 这里把多个 agent 的回复草稿汇总成最终回复，供后面的平台回写直接使用。
        final_reply = "\n".join(result.reply_draft for result in results if result.reply_draft).strip()
        if not final_reply:
            final_reply = "No reply was generated."

        write_back_actions = await self._write_back_if_needed(event, route, results, final_reply)

        return OrchestratorResult(
            execution_id=execution_id,
            status="success",
            route=route,
            summary=f"Plan executed in {plan.mode} mode with {len(plan.steps)} step(s).",
            results=results,
            final_reply=final_reply,
            write_back_actions=write_back_actions,
        )

    async def _write_back_if_needed(
        self,
        event: UnifiedEvent,
        route: str,
        results: list[AgentResult],
        final_reply: str,
    ) -> list[str]:
        # 这个函数的作用是判断当前结果是否需要回写原平台，并记录回写结果。
        if event.platform != "qq":
            return []

        # 这里先构建回写参数，再真正调用发送工具，避免无意义的外部调用。
        payload = self._build_write_back_payload(event, route, results, final_reply)
        if payload is None:
            return []

        try:
            send_tool = self.tools.get("send_qq_message")
            response = await send_tool.execute(**payload)
            for result in results:
                if result.agent == "inbox_dispatch":
                    # 把真实发送动作补记到 tool_calls 里，方便后面调试和审计。
                    result.tool_calls.append(
                        ToolCallRecord(
                            tool="send_qq_message",
                            arguments=payload,
                        )
                    )
                    break
            return [f"qq_write_back_sent:{response.get('status', 'unknown')}"]
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
    ) -> dict[str, object] | None:
        # 这个函数的作用是按不同场景生成平台回写参数，没有必要回写时直接返回空。
        if route == "message_dispatch":
            # 普通群消息先走快慢通道判断，只有需要提醒时才真正回写。
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
            # 私聊默认允许直接回复，只要 agent 产出了有效内容。
            if not final_reply or final_reply == "No reply was generated.":
                return None
            return {
                "chat_type": "private",
                "chat_id": event.chat_id,
                "message": final_reply,
            }

        if self._is_at_self(event):
            # 群里被 @ 时，统一使用 at + 文本分段的方式回写。
            if not final_reply or final_reply == "No reply was generated.":
                return None
            return {
                "chat_type": "group",
                "chat_id": event.chat_id,
                "segments": [
                    {"type": "at", "data": {"qq": event.sender.id}},
                    {"type": "text", "data": {"text": f" {final_reply}"}},
                ],
            }

        return None

    @staticmethod
    def _is_at_self(event: UnifiedEvent) -> bool:
        # 这个函数的作用是判断当前消息是否明确 @ 到机器人自身。
        if not event.self_id:
            return False
        return event.self_id in event.mentions
