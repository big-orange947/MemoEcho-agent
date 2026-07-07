from __future__ import annotations

from app.agents.base import BaseAgent
from app.agents.schedule_extractor import ScheduleExtractor
from app.schemas.results import AgentResult, ToolCallRecord
from app.schemas.tasks import AgentTaskContext


class ScheduleAgent(BaseAgent):
    name = "schedule"

    def __init__(self, tools) -> None:
        super().__init__(tools)
        # ScheduleExtractor 负责把自然语言消息整理成标准日程候选对象。
        self.extractor = ScheduleExtractor()

    async def run(self, task_context: AgentTaskContext, action: str) -> AgentResult:
        text = task_context.event.text or ""
        candidate = self.extractor.extract(text)
        # 先把提取结果整理成结构化输出，即使后面落库失败也能保留结果。
        extracted = {
            "title": candidate.title,
            "start_time": candidate.start_time,
            "end_time": candidate.end_time,
            "location": candidate.location,
            "content": candidate.content,
            "participants": candidate.participants,
            "confidence": candidate.confidence,
            "source_chat_id": task_context.event.chat_id,
        }
        tool_calls: list[ToolCallRecord] = []
        next_actions: list[str] = []

        if candidate.start_time:
            # 这里的 payload 直接对齐 Java schedule-service 的接口字段。
            payload = {
                "sourceEventId": task_context.event.event_id,
                "platform": task_context.event.platform,
                "chatId": task_context.event.chat_id,
                "senderId": task_context.event.sender.id,
                "title": candidate.title,
                "startTime": candidate.start_time,
                "endTime": candidate.end_time,
                "location": candidate.location,
                "content": candidate.content,
                "participants": candidate.participants,
                "confidence": candidate.confidence,
            }
            tool_calls.append(ToolCallRecord(tool="create_schedule", arguments=payload))
            try:
                # 真正的持久化副作用统一通过 tool 层执行。
                create_schedule_tool = self.tools.get("create_schedule")
                persistence_result = await create_schedule_tool.execute(payload=payload)
                extracted["persisted_schedule"] = persistence_result
            except KeyError:
                next_actions.append("create_schedule tool is not registered")
            except Exception as exc:
                extracted["persistence_error"] = str(exc)
                next_actions.append("retry_schedule_persistence")

        # 最后总是生成一段可以回写平台的说明文字。
        reply = self._build_reply(candidate, extracted)
        return AgentResult(
            task_id=task_context.task_id,
            agent=self.name,
            status="success",
            structured_result=extracted,
            reply_draft=reply,
            tool_calls=tool_calls,
            next_actions=next_actions,
        )

    def _build_reply(self, candidate, extracted: dict) -> str:
        # 这里根据“是否提取到时间”“是否落库成功”调整反馈语气。
        persisted = "persisted_schedule" in extracted
        if candidate.start_time and candidate.location and persisted:
            return f"已记录日程：{candidate.title}，开始时间 {candidate.start_time}，地点 {candidate.location}。"
        if candidate.start_time and persisted:
            return f"已记录日程：{candidate.title}，开始时间 {candidate.start_time}。"
        if candidate.start_time and candidate.location:
            return f"已提取候选日程：{candidate.title}，开始时间 {candidate.start_time}，地点 {candidate.location}。"
        if candidate.start_time:
            return f"已提取候选日程：{candidate.title}，开始时间 {candidate.start_time}。"
        return f"已识别到候选日程主题：{candidate.title}，但时间信息还不完整。"
