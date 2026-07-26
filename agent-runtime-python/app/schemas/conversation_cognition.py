from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.conversation_progress import ConversationProgressMessage


class CognitionFieldResult(BaseModel):
    """表示认知卡中的一个可解释字段，置信度只描述当前聊天记录对该结论的支持程度。"""

    value: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class ConversationCognitionRequest(BaseModel):
    """接收 Event Center 提供的完整会话范围和已校正身份的双向消息时间线。"""

    user_id: str = Field(alias="userId")
    platform: str
    chat_type: str = Field(alias="chatType")
    chat_id: str = Field(alias="chatId")
    messages: list[ConversationProgressMessage] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


class ConversationCognitionResponse(BaseModel):
    """返回可直接合并到认知卡的结构化推断，不携带用户锁定状态和字段来源。"""

    relationship: CognitionFieldResult = Field(default_factory=CognitionFieldResult)
    preferred_address: CognitionFieldResult = Field(
        default_factory=CognitionFieldResult,
        alias="preferredAddress",
    )
    counterparty_traits: CognitionFieldResult = Field(
        default_factory=CognitionFieldResult,
        alias="counterpartyTraits",
    )
    owner_expression_habits: CognitionFieldResult = Field(
        default_factory=CognitionFieldResult,
        alias="ownerExpressionHabits",
    )
    counterparty_expression_habits: CognitionFieldResult = Field(
        default_factory=CognitionFieldResult,
        alias="counterpartyExpressionHabits",
    )
    background_summary: CognitionFieldResult = Field(
        default_factory=CognitionFieldResult,
        alias="backgroundSummary",
    )
    current_progress: CognitionFieldResult = Field(
        default_factory=CognitionFieldResult,
        alias="currentProgress",
    )
    known_facts: list[str] = Field(default_factory=list, alias="knownFacts")
    recent_topics: list[str] = Field(default_factory=list, alias="recentTopics")
    open_questions: list[str] = Field(default_factory=list, alias="openQuestions")
    source_event_ids: list[str] = Field(default_factory=list, alias="sourceEventIds")
    source_message_count: int = Field(default=0, alias="sourceMessageCount")
    generated_by_model: bool = Field(default=False, alias="generatedByModel")
    generated_at: str = Field(
        default_factory=lambda: datetime.now().astimezone().isoformat(),
        alias="generatedAt",
    )

    model_config = {"populate_by_name": True}
