from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseTool(ABC):
    name: str

    @abstractmethod
    async def execute(self, **kwargs: Any) -> Any:
        # 所有工具统一暴露 execute，方便 planner / orchestrator 直接按名字调度。
        raise NotImplementedError
