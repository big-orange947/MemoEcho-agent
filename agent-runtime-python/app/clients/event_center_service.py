from __future__ import annotations

import os
from typing import Any

import httpx


class EventCenterServiceClient:
    def __init__(self, base_url: str | None = None, timeout_seconds: float = 10.0) -> None:
        # 这个构造函数负责确定 event-center-service 的访问地址，方便本地联调和部署切换。
        self.base_url = (base_url or os.getenv("EVENT_CENTER_SERVICE_BASE_URL") or "http://127.0.0.1:8093").rstrip("/")
        self.timeout_seconds = timeout_seconds

    async def list_conversation_messages(
        self,
        chat_id: str,
        platform: str | None = None,
        chat_type: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        # 这个函数的作用是查询某个会话最近的结构化消息，供摘要、补上下文和统一收件箱复用。
        params: dict[str, Any] = {"limit": limit}
        if platform:
            params["platform"] = platform
        if chat_type:
            params["chatType"] = chat_type

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.get(
                f"{self.base_url}/internal/conversations/{chat_id}/messages",
                params=params,
            )
            response.raise_for_status()
            return response.json()
