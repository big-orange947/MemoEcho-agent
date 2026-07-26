from __future__ import annotations

import os
from typing import Any

import httpx


class ConnectorServiceClient:
    def __init__(self, base_url: str | None = None, timeout_seconds: float = 10.0) -> None:
        # 统一封装 Java connector-service 的访问入口，避免各个 Agent 到处手写 HTTP 地址。
        self.base_url = (base_url or os.getenv("CONNECTOR_SERVICE_BASE_URL") or "http://127.0.0.1:8091").rstrip("/")
        self.timeout_seconds = timeout_seconds

    async def send_group_message(
        self,
        group_id: int,
        message: str | None = None,
        segments: list[dict[str, Any]] | None = None,
        client_message_id: str | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"groupId": group_id}
        if segments:
            # segments 优先级更高，适合 @ 人、混合文本、卡片等结构化发送场景。
            payload["segments"] = segments
        else:
            payload["message"] = message or ""
        if client_message_id:
            payload["clientMessageId"] = client_message_id
        if correlation_id:
            payload["correlationId"] = correlation_id

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(f"{self.base_url}/internal/napcat/messages/group", json=payload)
            response.raise_for_status()
            return response.json()

    async def send_private_message(
        self,
        user_id: int,
        message: str | None = None,
        segments: list[dict[str, Any]] | None = None,
        client_message_id: str | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"userId": user_id}
        if segments:
            payload["segments"] = segments
        else:
            payload["message"] = message or ""
        if client_message_id:
            payload["clientMessageId"] = client_message_id
        if correlation_id:
            payload["correlationId"] = correlation_id

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(f"{self.base_url}/internal/napcat/messages/private", json=payload)
            response.raise_for_status()
            return response.json()

    async def query_group(self, action: str, group_id: int) -> dict[str, Any]:
        """调用 Connector 暴露的只读群接口，不接受任意 URL。"""
        paths = {
            "group_info": f"/internal/napcat/groups/{group_id}",
            "member_list": f"/internal/napcat/groups/{group_id}/members",
            "notice_list": f"/internal/napcat/groups/{group_id}/notices",
            "shut_list": f"/internal/napcat/groups/{group_id}/shut-list",
            "essence_list": f"/internal/napcat/groups/{group_id}/essence-messages",
            "file_list": f"/internal/napcat/groups/{group_id}/files",
        }
        path = paths.get(action)
        if path is None:
            raise ValueError(f"Unsupported group query: {action}")
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.get(f"{self.base_url}{path}")
            response.raise_for_status()
            return response.json()

    async def execute_group_operation(self, operation: dict[str, Any]) -> dict[str, Any]:
        """把审批后的规范化动作交给 Java Connector，禁止直接传 NapCat action。"""
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                f"{self.base_url}/internal/napcat/groups/operations",
                json=operation,
            )
            response.raise_for_status()
            return response.json()
