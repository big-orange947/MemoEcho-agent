from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class Sender(BaseModel):
    id: str
    name: str
    role: str | None = None


class Attachment(BaseModel):
    file_id: str | None = None
    file_name: str | None = None
    file_type: str | None = None
    url: str | None = None


class UnifiedEvent(BaseModel):
    event_id: str = Field(alias="eventId")
    platform: str
    scene: str | None = None
    event_type: str = Field(alias="eventType")
    chat_type: str = Field(alias="chatType")
    chat_id: str = Field(alias="chatId")
    self_id: str | None = Field(default=None, alias="selfId")
    sender: Sender
    text: str | None = ""
    attachments: list[Attachment] = []
    mentions: list[str] = []
    timestamp: str
    raw_payload: dict[str, Any] = Field(default_factory=dict, alias="rawPayload")

    model_config = {
        "populate_by_name": True
    }
