from __future__ import annotations

import asyncio
from typing import Any

from langchain_core.tools import BaseTool

from app.tools.base import ToolExecutionContext, ToolSpec


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}
        self._specs: dict[str, ToolSpec] = {}
        # 内部服务不属于 Agent 可调用工具，只给审批回调等受控入口使用。
        self._internal_services: dict[str, Any] = {}
        self._completed_calls: dict[str, Any] = {}
        self._call_locks: dict[str, asyncio.Lock] = {}

    def register(self, tool: BaseTool, spec: ToolSpec) -> None:
        """注册一个 LangChain ``BaseTool``，并保存独立的权限元数据。"""
        if not isinstance(tool, BaseTool):
            raise TypeError("ToolRegistry only accepts LangChain BaseTool instances")
        if tool.name != spec.name:
            raise ValueError(f"tool name and spec name differ: {tool.name} != {spec.name}")
        self._tools[tool.name] = tool
        self._specs[tool.name] = spec

    def get(self, name: str) -> Any:
        return self._tools[name]

    def register_internal_service(self, name: str, service: Any) -> None:
        """注册不对 Agent 暴露的内部服务，避免审批入口绕过工具权限模型。"""
        normalized_name = str(name or "").strip()
        if not normalized_name:
            raise ValueError("internal service name is required")
        self._internal_services[normalized_name] = service

    def get_internal_service(self, name: str) -> Any:
        """读取仅供 Runtime 内部使用的服务实例。"""
        return self._internal_services[name]

    def names(self) -> list[str]:
        return sorted(self._tools.keys())

    def specs(self) -> list[ToolSpec]:
        """返回所有已注册工具的能力清单，供 Planner 和前端展示真实可用能力。"""
        return [self._specs[name] for name in self.names()]

    async def ainvoke(
        self,
        name: str,
        *,
        context: ToolExecutionContext,
        idempotency_key: str = "",
        arguments: dict[str, Any] | None = None,
    ) -> Any:
        """统一校验权限并通过 LangChain ``ainvoke`` 执行工具。"""
        tool = self.get(name)
        if not context.trusted_internal and name not in context.allowed_tools:
            raise PermissionError(f"tool is not authorized: {name}")

        normalized_arguments = dict(arguments or {})
        normalized_key = str(idempotency_key or "").strip()
        if not normalized_key:
            return await tool.ainvoke(normalized_arguments)
        if normalized_key in self._completed_calls:
            return self._completed_calls[normalized_key]

        lock = self._call_locks.setdefault(normalized_key, asyncio.Lock())
        async with lock:
            if normalized_key in self._completed_calls:
                return self._completed_calls[normalized_key]
            # 幂等键由注册表消费，避免把未声明字段注入工具输入 schema。
            result = await tool.ainvoke(normalized_arguments)
            self._completed_calls[normalized_key] = result
            return result
