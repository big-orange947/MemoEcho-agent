from __future__ import annotations

from typing import Any


class CreateScheduleTool:
    """日程服务的底层适配器；真正暴露给 Agent 的入口在 LangChain @tool 中。"""

    name = "create_schedule"

    def __init__(self, schedule_service_client: Any) -> None:
        self.schedule_service_client = schedule_service_client

    async def create(self, *, payload: dict[str, Any]) -> Any:
        """校验并转发日程创建请求。"""
        if not isinstance(payload, dict):
            raise ValueError("payload is required for create_schedule")
        return await self.schedule_service_client.create_schedule(payload)
