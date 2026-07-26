from __future__ import annotations

from typing import Any


class ListTasksTool:
    """任务列表读取适配器；用于 Agent 决策但不改变任务状态。"""

    name = "list_tasks"

    def __init__(self, task_service_client: Any) -> None:
        self.task_service_client = task_service_client

    async def list(self, *, params: dict[str, Any] | None = None) -> Any:
        """读取任务列表，并把筛选条件原样透传给 task-service。"""
        params = params or {}
        if not isinstance(params, dict):
            raise ValueError("params must be a dict for list_tasks")
        return await self.task_service_client.list_tasks(params)
