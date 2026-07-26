from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class Sender(BaseModel):
    id: str
    name: str
    role: str | None = None


class Attachment(BaseModel):
    # Event Center 按 Java record 的默认规则输出 camelCase；同时兼容本地工具使用 snake_case。
    file_id: str | None = Field(default=None, alias="fileId")
    file_name: str | None = Field(default=None, alias="fileName")
    file_type: str | None = Field(default=None, alias="fileType")
    url: str | None = None

    model_config = {
        "populate_by_name": True,
    }


class MessageSegment(BaseModel):
    """保存平台消息段；data 保持开放结构以兼容 NapCat 后续增加的新类型。"""

    type: str
    data: dict[str, Any] = Field(default_factory=dict)


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
    segments: list[MessageSegment] = Field(default_factory=list)
    timestamp: str
    raw_payload: dict[str, Any] = Field(default_factory=dict, alias="rawPayload")
    # 参与者身份由连接器在事件入口统一判定，Runtime 不再仅凭 senderId 猜测。
    actor_type: str | None = Field(default=None, alias="actorType")
    platform_message_id: str | None = Field(default=None, alias="platformMessageId")
    client_message_id: str | None = Field(default=None, alias="clientMessageId")
    correlation_id: str | None = Field(default=None, alias="correlationId")
    sequence: int | None = None
    # sentAt 表示平台实际发送时间；receivedAt 表示 Event Center 到达时间；importedAt 表示导入时间。
    # 三者不能混用，否则延迟 Webhook 和历史导入会破坏对话先后关系。
    sent_at: str | None = Field(default=None, alias="sentAt")
    received_at: str | None = Field(default=None, alias="receivedAt")
    imported_at: str | None = Field(default=None, alias="importedAt")
    direction: str | None = None
    delegated_task_id: str | None = Field(default=None, alias="delegatedTaskId")

    model_config = {
        "populate_by_name": True
    }
