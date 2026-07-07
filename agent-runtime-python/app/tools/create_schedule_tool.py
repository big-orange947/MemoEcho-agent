from __future__ import annotations

from typing import Any

from app.tools.base import BaseTool


class CreateScheduleTool(BaseTool):
    name = "create_schedule"

    def __init__(self, schedule_service_client: Any) -> None:
        self.schedule_service_client = schedule_service_client

    async def execute(self, **kwargs: Any) -> Any:
        # 只有 tool 层允许真正产生外部副作用，这里负责发起日程创建请求。
        payload = kwargs.get("payload")
        if not isinstance(payload, dict):
            raise ValueError("payload is required for create_schedule")
        return await self.schedule_service_client.create_schedule(payload)
