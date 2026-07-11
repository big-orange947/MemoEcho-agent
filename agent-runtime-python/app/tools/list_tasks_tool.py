from __future__ import annotations

from typing import Any

from app.tools.base import BaseTool


class ListTasksTool(BaseTool):
    name = "list_tasks"

    def __init__(self, task_service_client: Any) -> None:
        # 这个构造函数的作用是注入 task-service 客户端，让工具层统一负责查询外部服务。
        self.task_service_client = task_service_client

    async def execute(self, **kwargs: Any) -> Any:
        # 这个函数的作用是读取任务列表，并把筛选参数原样透传给 task-service。
        params = kwargs.get("params") or {}
        if not isinstance(params, dict):
            raise ValueError("params must be a dict for list_tasks")
        return await self.task_service_client.list_tasks(params)
