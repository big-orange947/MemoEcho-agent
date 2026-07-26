from __future__ import annotations

from typing import Any


class CreateTaskTool:
    """任务服务的底层适配器；真正暴露给 Agent 的入口在 LangChain @tool 中。"""

    name = "create_task"

    def __init__(self, task_service_client: Any) -> None:
        self.task_service_client = task_service_client

    async def create(self, *, payload: dict[str, Any]) -> Any:
        """校验并转发任务创建请求。"""
        if not isinstance(payload, dict):
            raise ValueError("payload is required for create_task")
        return await self.task_service_client.create_task(payload)
