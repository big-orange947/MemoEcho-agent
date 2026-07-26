from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class GroupOperationApprovalResponse(BaseModel):
    """返回审批后的实际平台执行状态。"""

    status: str
    action: str
    risk: str
    platform_result: dict[str, Any] = Field(alias="platformResult")

    model_config = {"populate_by_name": True}


class GroupOperationEventApprovalRequest(BaseModel):
    """桌面端按事件审批时只提交确认短语，不接触内部审批令牌。"""

    confirmation_text: str = Field(alias="confirmationText", min_length=1, max_length=200)

    model_config = {"populate_by_name": True}


class PendingGroupOperationResponse(BaseModel):
    """待审批群操作的安全展示模型，刻意不包含一次性令牌。"""

    event_id: str = Field(alias="eventId")
    action: str
    risk: str
    confirmation_phrase: str = Field(alias="confirmationPhrase")
    expires_at: str = Field(alias="expiresAt")
    operation: dict[str, Any] = Field(default_factory=dict)

    model_config = {"populate_by_name": True}
