from __future__ import annotations

import os
from typing import Any

import httpx


class TaskServiceClient:
    def __init__(self, base_url: str | None = None, timeout_seconds: float = 10.0) -> None:
        # base_url 做成可配置，方便本地开发和后续容器部署使用不同地址。
        self.base_url = (base_url or os.getenv("TASK_SERVICE_BASE_URL") or "http://127.0.0.1:8094").rstrip("/")
        self.timeout_seconds = timeout_seconds

    async def create_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(f"{self.base_url}/internal/tasks", json=payload)
            response.raise_for_status()
            return response.json()

    async def list_tasks(self, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        # 后面“我今天有哪些待办”这类查询流程，会复用这个方法。
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.get(f"{self.base_url}/internal/tasks", params=params or {})
            response.raise_for_status()
            return response.json()
