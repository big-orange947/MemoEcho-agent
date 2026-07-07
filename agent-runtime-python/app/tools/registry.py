from __future__ import annotations

from typing import Any


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Any] = {}

    def register(self, name: str, tool: Any) -> None:
        # 注册表保持简单字典结构，当前阶段先优先保证可读性和可替换性。
        self._tools[name] = tool

    def get(self, name: str) -> Any:
        return self._tools[name]

    def names(self) -> list[str]:
        return sorted(self._tools.keys())
