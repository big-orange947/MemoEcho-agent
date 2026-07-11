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


class NotificationDecision(BaseModel):
    """
    通知决策是 Agent 交给事件中心和工作台的稳定契约。

    它只描述消息应如何展示和归并，不携带模型提示词、工具参数或用户密钥。
    """

    channel: str
    priority: str
    trigger_reason: str
    notify_now: bool
    aggregation_key: str
    aggregation_status: str
    buffered_count: int = 0
    summary_candidate: str = ""


class OrchestratorResult(BaseModel):
    execution_id: str
    status: str
    route: str
    summary: str
    results: list[AgentResult]
    final_reply: str
    write_back_actions: list[str] = []
    notification: NotificationDecision | None = None
