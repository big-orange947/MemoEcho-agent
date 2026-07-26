from __future__ import annotations

from typing import Any


class GetRecentMessagesTool:
    """会话历史读取适配器；用于给 LangChain 工具闭包提供受控数据源。"""

    name = "get_recent_messages"

    def __init__(self, event_center_service_client: Any) -> None:
        self.event_center_service_client = event_center_service_client

    async def fetch(
        self,
        *,
        chat_id: str,
        platform: str | None = None,
        chat_type: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """读取指定会话最近消息，供 Agent 恢复上下文。"""
        if not chat_id:
            raise ValueError("chat_id is required")
        return await self.event_center_service_client.list_conversation_messages(
            chat_id=str(chat_id),
            platform=platform,
            chat_type=chat_type,
            limit=int(limit),
        )
