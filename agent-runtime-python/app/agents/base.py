from __future__ import annotations

from abc import ABC, abstractmethod

from app.schemas.results import AgentResult
from app.schemas.tasks import AgentTaskContext
from app.tools.registry import ToolRegistry


class BaseAgent(ABC):
    name: str

    def __init__(self, tools: ToolRegistry) -> None:
        self.tools = tools

    @abstractmethod
    async def run(self, task_context: AgentTaskContext, action: str) -> AgentResult:
        raise NotImplementedError

