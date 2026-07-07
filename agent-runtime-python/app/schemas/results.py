from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class ToolCallRecord(BaseModel):
    tool: str
    arguments: dict[str, Any] = {}


class AgentResult(BaseModel):
    task_id: str
    agent: str
    status: str
    structured_result: dict[str, Any] = {}
    reply_draft: str = ""
    tool_calls: list[ToolCallRecord] = []
    next_actions: list[str] = []
    need_confirmation: bool = False


class OrchestratorResult(BaseModel):
    execution_id: str
    status: str
    route: str
    summary: str
    results: list[AgentResult]
    final_reply: str
    write_back_actions: list[str] = []

