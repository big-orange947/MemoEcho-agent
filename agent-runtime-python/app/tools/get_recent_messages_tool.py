from __future__ import annotations

from typing import Any

from app.tools.base import BaseTool


class GetRecentMessagesTool(BaseTool):
    name = "get_recent_messages"

    def __init__(self, event_center_service_client: Any) -> None:
        # 这个构造函数的作用是注入 event-center 查询 client，让工具层只负责参数转发。
        self.event_center_service_client = event_center_service_client

    async def execute(self, **kwargs: Any) -> list[dict[str, Any]]:
        # 这个函数的作用是统一查询最近消息，屏蔽底层 HTTP 参数细节。
        chat_id = kwargs.get("chat_id")
        platform = kwargs.get("platform")
        chat_type = kwargs.get("chat_type")
        limit = kwargs.get("limit", 20)

        if not chat_id:
            raise ValueError("chat_id is required")

        return await self.event_center_service_client.list_conversation_messages(
            chat_id=str(chat_id),
            platform=platform,
            chat_type=chat_type,
            limit=int(limit),
        )
