from __future__ import annotations

from pydantic import BaseModel, Field


class OpenConversationItem(BaseModel):
    """表示一条仍需要当前责任方处理的原始会话消息。"""

    source_event_id: str = Field(alias="sourceEventId")
    actor_type: str = Field(alias="actorType")
    text: str
    timestamp: str = ""
    reason: str

    model_config = {
        "populate_by_name": True,
    }


class ConversationOpenState(BaseModel):
    """保存由可信事件时间线推导出的当前会话开放状态。"""

    status: str = "IDLE"
    responsible_party: str = Field(default="NONE", alias="responsibleParty")
    summary: str = "当前没有尚待处理的会话消息"
    source_event_ids: list[str] = Field(default_factory=list, alias="sourceEventIds")
    pending_items: list[OpenConversationItem] = Field(default_factory=list, alias="pendingItems")

    model_config = {
        "populate_by_name": True,
    }
