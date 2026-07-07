from app.agents.base import BaseAgent
from app.schemas.results import AgentResult
from app.schemas.tasks import AgentTaskContext


class SocialAgent(BaseAgent):
    name = "social"

    async def run(self, task_context: AgentTaskContext, action: str) -> AgentResult:
        draft = "A reply draft can be generated here."
        return AgentResult(
            task_id=task_context.task_id,
            agent=self.name,
            status="success",
            structured_result={"draft": draft},
            reply_draft=draft,
        )

