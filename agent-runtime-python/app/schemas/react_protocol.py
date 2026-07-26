from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


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
    conversation_timeline: list[dict[str, str]] = field(default_factory=list)
    pre_task_context: list[dict[str, str]] = field(default_factory=list)
    working_memory: dict[str, Any] = field(default_factory=dict)
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
            "historyAccessAllowed": self.history_access_allowed,
            "availableTools": self.available_tools,
        }


@dataclass(frozen=True)
class CandidateReplyGuardResult:
    """候选回复经过控制字段泄漏检查后的结果。"""

    allowed: bool
    reasons: tuple[str, ...] = ()
