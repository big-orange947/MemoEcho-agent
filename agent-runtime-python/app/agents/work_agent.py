from app.agents.base import BaseAgent
from app.agents.task_extractor import TaskExtractor
from app.schemas.results import AgentResult, ToolCallRecord
from app.schemas.tasks import AgentTaskContext


class WorkAgent(BaseAgent):
    name = "work"

    def __init__(self, tools) -> None:
        super().__init__(tools)
        # 提取逻辑单独放到 extractor 里，后面如果换成大模型提取，
        # 不需要重写整个 WorkAgent。
        self.extractor = TaskExtractor()

    async def run(self, task_context: AgentTaskContext, action: str) -> AgentResult:
        # 第一步：把输入消息整理成标准任务候选对象。
        candidate = self.extractor.extract(task_context.event.text or "")

        # 第二步：先组装结构化结果。这样即使后面落库失败，
        # orchestrator 仍然能拿到本次提取结果。
        plan = {
            "title": candidate.title,
            "description": candidate.description,
            "due_time": candidate.due_time,
            "priority": candidate.priority,
            "confidence": candidate.confidence,
            "actionable": candidate.actionable,
            "source_chat_id": task_context.event.chat_id,
        }
        tool_calls: list[ToolCallRecord] = []
        next_actions: list[str] = []

        if candidate.actionable:
            # 这里的 payload 直接对应 Java task-service 的接口契约。
            # agent 只负责提取，不负责存储细节。
            payload = {
                "sourceEventId": task_context.event.event_id,
                "platform": task_context.event.platform,
                "chatId": task_context.event.chat_id,
                "senderId": task_context.event.sender.id,
                "title": candidate.title,
                "description": candidate.description,
                "dueTime": candidate.due_time,
                "priority": candidate.priority,
                "status": "pending",
                "confidence": candidate.confidence,
            }
            tool_calls.append(ToolCallRecord(tool="create_task", arguments=payload))
            try:
                # 第三步：通过 tool 层执行真正的落库副作用。
                create_task_tool = self.tools.get("create_task")
                persistence_result = await create_task_tool.execute(payload=payload)
                plan["persisted_task"] = persistence_result
            except KeyError:
                next_actions.append("create_task tool is not registered")
            except Exception as exc:
                plan["persistence_error"] = str(exc)
                next_actions.append("retry_task_persistence")

        # 第四步：无论是否落库成功，都生成一个可直接回写的平台回复草稿。
        reply = self._build_reply(candidate, plan)
        return AgentResult(
            task_id=task_context.task_id,
            agent=self.name,
            status="success",
            structured_result=plan,
            reply_draft=reply,
            tool_calls=tool_calls,
            next_actions=next_actions,
        )

    def _build_reply(self, candidate, plan: dict) -> str:
        # 这里故意只返回纯文本，避免 QQ 侧出现 Markdown 风格渲染不一致的问题。
        if not candidate.actionable:
            return "I did not detect a clear actionable task, so I kept this as normal conversation."
        if "persisted_task" in plan and candidate.due_time:
            return f"Task recorded: {candidate.title}. Due time: {candidate.due_time}."
        if "persisted_task" in plan:
            return f"Task recorded: {candidate.title}."
        if candidate.due_time:
            return f"Task extracted: {candidate.title}. Due time: {candidate.due_time}."
        return f"Task extracted: {candidate.title}."
