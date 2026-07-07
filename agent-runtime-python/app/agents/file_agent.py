from app.agents.base import BaseAgent
from app.schemas.results import AgentResult
from app.schemas.tasks import AgentTaskContext


class FileAgent(BaseAgent):
    name = "file"

    async def run(self, task_context: AgentTaskContext, action: str) -> AgentResult:
        attachment_names = [attachment.file_name for attachment in task_context.event.attachments if attachment.file_name]
        return AgentResult(
            task_id=task_context.task_id,
            agent=self.name,
            status="success",
            structured_result={"attachments": attachment_names},
            reply_draft="Attachments were analyzed at a placeholder level.",
        )

