from __future__ import annotations

from typing import Any

from app.tools.base import BaseTool


class SendQqMessageTool(BaseTool):
    name = "send_qq_message"

    def __init__(self, connector_service_client: Any) -> None:
        self.connector_service_client = connector_service_client

    async def execute(self, **kwargs: Any) -> Any:
        # 这个工具是 runtime 回写 QQ 的统一出口，后面接别的平台时可以保持同样接口风格。
        chat_type = kwargs.get("chat_type")
        chat_id = kwargs.get("chat_id")
        message = kwargs.get("message")
        segments = kwargs.get("segments")

        if chat_type not in {"group", "private"}:
            raise ValueError("chat_type must be group or private")
        if chat_id is None:
            raise ValueError("chat_id is required")
        if not message and not segments:
            raise ValueError("message or segments is required")

        if chat_type == "group":
            return await self.connector_service_client.send_group_message(
                group_id=int(chat_id),
                message=message,
                segments=segments,
            )

        return await self.connector_service_client.send_private_message(
            user_id=int(chat_id),
            message=message,
            segments=segments,
        )
