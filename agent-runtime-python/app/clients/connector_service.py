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
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"groupId": group_id}
        if segments:
            # segments 优先级更高，适合 @ 人、混合文本、卡片等结构化发送场景。
            payload["segments"] = segments
        else:
            payload["message"] = message or ""

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(f"{self.base_url}/internal/napcat/messages/group", json=payload)
            response.raise_for_status()
            return response.json()

    async def send_private_message(
        self,
        user_id: int,
        message: str | None = None,
        segments: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"userId": user_id}
        if segments:
            payload["segments"] = segments
        else:
            payload["message"] = message or ""

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(f"{self.base_url}/internal/napcat/messages/private", json=payload)
            response.raise_for_status()
            return response.json()
