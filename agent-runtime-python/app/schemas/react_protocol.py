from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class CompletionReflectionDecision(BaseModel):
    """描述完成复核节点的严格结构化输出。

    完成判断会影响是否继续向联系人发送消息，因此不能接受自由格式字典。
    字段别名保持与模型输出协议一致，Python 侧仍可使用蛇形命名读取。
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    should_complete: bool = Field(alias="shouldComplete")
    outcome: Literal["SUCCESS", "REJECTED", "BLOCKED"] = "SUCCESS"
    reason: str = ""
    progress_summary: str = Field(default="", alias="progressSummary")
    completion_report: str = Field(default="", alias="completionReport")
    final_message_instruction: str = Field(default="", alias="finalMessageInstruction")
    known_facts: list[str] = Field(default_factory=list, alias="knownFacts")
    pending_conditions: list[str] = Field(default_factory=list, alias="pendingConditions")
    evidence: list[str] = Field(default_factory=list)
    evidence_event_ids: list[str] = Field(default_factory=list, alias="evidenceEventIds")


@dataclass(frozen=True)
class ModelWorkingContext:
    """主控台 ReAct 节点可见的最小工作上下文。

    该对象只保存推理需要的任务事实和会话证据，不承载会话 ID、目标检索词、
    持久化状态 JSON 等控制面数据，避免这些字段误进入对外回复。
    """

    task_goal: str
    success_criteria: str
    deadline_text: str
    task_created_at: str
    current_time: str
    resolved_time_text: str
    conversation_timeline: list[dict[str, Any]] = field(default_factory=list)
    pre_task_context: list[dict[str, Any]] = field(default_factory=list)
    working_memory: dict[str, Any] = field(default_factory=dict)
    action_ledger: list[dict[str, Any]] = field(default_factory=list)
    history_access_allowed: bool = False
    available_tools: list[str] = field(default_factory=list)

    def to_model_payload(self) -> dict[str, Any]:
        """将安全工作上下文转换为模型调用所需的 JSON 对象。"""
        return {
            "taskGoal": self.task_goal,
            "successCriteria": self.success_criteria,
            "deadlineText": self.deadline_text,
            "taskCreatedAt": self.task_created_at,
            "currentTime": self.current_time,
            "resolvedTimeText": self.resolved_time_text,
            "conversationTimeline": self.conversation_timeline,
            "preTaskContext": self.pre_task_context,
            "workingMemory": self.working_memory,
            "actionLedger": self.action_ledger,
            "historyAccessAllowed": self.history_access_allowed,
            "availableTools": self.available_tools,
        }


@dataclass(frozen=True)
class CandidateReplyGuardResult:
    """候选回复经过控制字段泄漏检查后的结果。"""

    allowed: bool
    reasons: tuple[str, ...] = ()
