from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class VerifiedMemory(BaseModel):
    """表示 Event Center 已由用户确认且当前仍有效的一条长期记忆。"""

    id: str
    subject: str
    predicate: str
    value: str
    scope_type: str = Field(alias="scopeType")
    platform: str = ""
    scene: str = ""
    chat_type: str = Field(default="", alias="chatType")
    chat_id: str = Field(default="", alias="chatId")
    source_event_ids: list[str] = Field(default_factory=list, alias="sourceEventIds")
    source_actor_type: str = Field(default="", alias="sourceActorType")
    fact_authority: str = Field(default="", alias="factAuthority")
    confidence: float = 0.0
    status: str = "VERIFIED"
    expires_at: datetime | None = Field(default=None, alias="expiresAt")
    updated_at: datetime | None = Field(default=None, alias="updatedAt")

    model_config = {"populate_by_name": True}


class ExtractedMemoryCandidate(BaseModel):
    """表示模型从一条 OWNER 真人消息中提取出的、尚未确认的稳定事实。"""

    predicate: str
    value: str
    confidence: float = 0.0
    expires_at: datetime | None = Field(default=None, alias="expiresAt")

    model_config = {"populate_by_name": True}


class MemoryCandidateExtraction(BaseModel):
    """约束模型提取响应，禁止自由文本直接进入长期记忆仓库。"""

    candidates: list[ExtractedMemoryCandidate] = Field(default_factory=list)

    model_config = {"populate_by_name": True}
