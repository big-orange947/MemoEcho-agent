from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from app.schemas.events import UnifiedEvent


class AgentTaskContext(BaseModel):
    task_id: str
    route: str
    event: UnifiedEvent
    history_context: list[dict[str, Any]] = []
    retrieved_knowledge: list[dict[str, Any]] = []
    allowed_tools: list[str] = []
    execution_mode: str = "suggest_only"
    metadata: dict[str, Any] = {}


class PlanStep(BaseModel):
    agent: str
    action: str
    parallel_group: str | None = None


class ExecutionPlan(BaseModel):
    mode: str
    steps: list[PlanStep]

