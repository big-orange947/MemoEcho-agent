from __future__ import annotations

from typing import Any


class SendQqMessageTool:
    """QQ 发送适配器；真正暴露给 Agent 的入口在 LangChain @tool 中。"""

    name = "send_qq_message"

    def __init__(self, connector_service_client: Any) -> None:
        self.connector_service_client = connector_service_client

    async def send(
        self,
        *,
        chat_type: str,
        chat_id: str,
        message: str | None = None,
        segments: list[dict[str, Any]] | None = None,
        client_message_id: str | None = None,
        correlation_id: str | None = None,
    ) -> Any:
        """向 QQ 私聊或群聊发送文本/消息段。"""
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
                client_message_id=client_message_id,
                correlation_id=correlation_id,
            )

        return await self.connector_service_client.send_private_message(
            user_id=int(chat_id),
            message=message,
            segments=segments,
            client_message_id=client_message_id,
            correlation_id=correlation_id,
        )
