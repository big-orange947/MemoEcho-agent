from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class ScheduleIntent(str, Enum):
    """描述一条消息对日程系统的真实意图，避免把查询、取消和闲聊误当成新增日程。"""

    CREATE = "CREATE"
    UPDATE = "UPDATE"
    CANCEL = "CANCEL"
    QUERY = "QUERY"
    NONE = "NONE"
    AMBIGUOUS = "AMBIGUOUS"


class ScheduleCandidateStatus(str, Enum):
    """描述候选日程经过证据和时间校验后的可执行状态。"""

    CONFIRMED = "CONFIRMED"
    DRAFT = "DRAFT"
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    REJECTED = "REJECTED"


class StructuredScheduleItem(BaseModel):
    """承接 LLM 输出的单个日程候选；字段仍需经过本地校验后才能使用。"""

    title: str = ""
    date_text: str = Field(default="", alias="dateText")
    start_time_text: str = Field(default="", alias="startTimeText")
    end_time_text: str = Field(default="", alias="endTimeText")
    normalized_start_time: str = Field(default="", alias="normalizedStartTime")
    normalized_end_time: str = Field(default="", alias="normalizedEndTime")
    location: str = ""
    participants: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list, alias="missingFields")
    confidence: float = 0.0

    model_config = {
        "populate_by_name": True,
    }


class StructuredScheduleExtraction(BaseModel):
    """定义日程结构化抽取的稳定 JSON 契约，隔离不同模型供应商的自由文本差异。"""

    intent: ScheduleIntent = ScheduleIntent.NONE
    negated: bool = False
    events: list[StructuredScheduleItem] = Field(default_factory=list)
    confidence: float = 0.0

    model_config = {
        "populate_by_name": True,
    }


class SemanticIntentDecision(BaseModel):
    """保存向量意图门控结果；分数不足时必须交给后续层判断，不能强行改路由。"""

    label: str = "unknown"
    route: str | None = None
    score: float = 0.0
    margin: float = 0.0
    decisive: bool = False

