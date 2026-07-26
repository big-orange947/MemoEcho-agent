from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ConversationProgressMessage(BaseModel):
    """会话进度分析所需的最小消息结构，兼容 Java 返回的 camelCase 字段。"""

    event_id: str = Field(default="", alias="eventId")
    sender_name: str = Field(default="", alias="senderName")
    sender_role: str = Field(default="", alias="senderRole")
    text: str = ""
    timestamp: str = ""
    processing_status: str = Field(default="", alias="processingStatus")
    need_human_confirmation: bool = Field(default=False, alias="needHumanConfirmation")
    message_origin: str = Field(default="EXTERNAL", alias="messageOrigin")
    attachments: list[dict[str, Any]] = Field(default_factory=list)

    model_config = {
        "populate_by_name": True,
    }


class ConversationProgressRequest(BaseModel):
    """桌面端按需请求某个会话当前进度时传入的上下文。"""

    user_id: str = Field(alias="userId")
    platform: str
    chat_type: str = Field(alias="chatType")
    chat_id: str = Field(alias="chatId")
    messages: list[ConversationProgressMessage] = Field(default_factory=list)

    model_config = {
        "populate_by_name": True,
    }


class ConversationProgressResponse(BaseModel):
    """返回给 Event Center 的会话进度摘要，不包含任何可执行动作。"""

    summary: str
    generated_by_model: bool = Field(alias="generatedByModel")
    generated_at: str = Field(alias="generatedAt")

    model_config = {
        "populate_by_name": True,
    }
