from __future__ import annotations

import os
from typing import Any

import httpx


class ScheduleServiceClient:
    def __init__(self, base_url: str | None = None, timeout_seconds: float = 10.0) -> None:
        # base_url 做成可配置，方便本地联调和后续部署切换地址。
        self.base_url = (base_url or os.getenv("SCHEDULE_SERVICE_BASE_URL") or "http://127.0.0.1:8092").rstrip("/")
        self.timeout_seconds = timeout_seconds

    async def create_schedule(self, payload: dict[str, Any]) -> dict[str, Any]:
        # 这里负责把标准日程 payload 发给 Java schedule-service。
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(f"{self.base_url}/internal/schedules", json=payload)
            response.raise_for_status()
            return response.json()

    async def list_schedules(self, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        # 后面“最近有什么日程”这类问答流程会复用这个查询方法。
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.get(f"{self.base_url}/internal/schedules", params=params or {})
            response.raise_for_status()
            return response.json()
