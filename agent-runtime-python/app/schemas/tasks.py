from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.schemas.conversation_state import ConversationOpenState
from app.schemas.memories import VerifiedMemory
from app.schemas.events import UnifiedEvent


class AgentTaskContext(BaseModel):
    """封装一次 Agent 执行所需的事件、可信上下文、开放状态和工具权限。"""

    task_id: str
    route: str
    event: UnifiedEvent
    history_context: list[dict[str, Any]] = Field(default_factory=list)
    conversation_state: ConversationOpenState | None = None
    verified_memories: list[VerifiedMemory] = Field(default_factory=list)
    # 图谱检索的长期记忆线索（06 文档 P-C）：低权威，供话题连贯与事实参考，不单独证明现实状态。
    graph_memories: list[dict[str, Any]] = Field(default_factory=list)
    retrieved_knowledge: list[dict[str, Any]] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    execution_mode: str = "suggest_only"
    metadata: dict[str, Any] = Field(default_factory=dict)


class PlanStep(BaseModel):
    agent: str
    action: str
    parallel_group: str | None = None


class ExecutionPlan(BaseModel):
    mode: str
    steps: list[PlanStep]
