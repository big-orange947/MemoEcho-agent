from __future__ import annotations

import os
from typing import Any

import httpx


class TaskServiceClient:
    def __init__(self, base_url: str | None = None, timeout_seconds: float = 10.0) -> None:
        # 这个构造函数的作用是集中管理 task-service 地址，方便本地开发和部署环境切换。
        self.base_url = (base_url or os.getenv("TASK_SERVICE_BASE_URL") or "http://127.0.0.1:8094").rstrip("/")
        self.timeout_seconds = timeout_seconds

    async def create_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        # 这个函数的作用是调用 task-service 的创建接口，把提取出的任务持久化下来。
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(f"{self.base_url}/internal/tasks", json=payload)
            response.raise_for_status()
            return response.json()

    async def list_tasks(self, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        # 这个函数的作用是按条件拉取任务列表，供今日工作计划和待办查询场景复用。
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.get(f"{self.base_url}/internal/tasks", params=params or {})
            response.raise_for_status()
            return response.json()
