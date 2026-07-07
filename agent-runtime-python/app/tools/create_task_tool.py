from __future__ import annotations

from typing import Any

from app.tools.base import BaseTool


class CreateTaskTool(BaseTool):
    name = "create_task"

    def __init__(self, task_service_client: Any) -> None:
        self.task_service_client = task_service_client

    async def execute(self, **kwargs: Any) -> Any:
        # 只有 tool 层允许真正产生外部副作用。
        # agent 传入业务数据，IO 调用由 tool 统一负责。
        payload = kwargs.get("payload")
        if not isinstance(payload, dict):
            raise ValueError("payload is required for create_task")
        return await self.task_service_client.create_task(payload)
