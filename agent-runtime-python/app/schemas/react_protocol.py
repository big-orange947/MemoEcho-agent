from __future__ import annotations

from dataclasses import dataclass
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
class CandidateReplyGuardResult:
    """候选回复经过控制字段泄漏检查后的结果。"""

    allowed: bool
    reasons: tuple[str, ...] = ()
