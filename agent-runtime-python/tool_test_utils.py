from __future__ import annotations

import inspect
from typing import Any

from langchain_core.tools import BaseTool
from pydantic import ConfigDict, Field

from app.tools.base import ToolExecutionContext, ToolSpec
from app.tools.registry import ToolRegistry


class LegacyTestTool(BaseTool):
    """把旧测试桩包装成 LangChain 工具，避免生产代码继续保留旧 execute 协议。"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    description: str = "测试用 LangChain 工具适配器"
    legacy: Any = Field(exclude=True)

    def _run(self, *args: Any, **kwargs: Any) -> Any:
        """同步调用不在测试链路中使用，防止误把异步工具当同步工具执行。"""
        raise RuntimeError("测试工具只支持异步调用")

    async def _arun(self, *args: Any, **kwargs: Any) -> Any:
        """按新注册协议执行旧测试桩，兼容各类工具适配器的真实方法名。"""
        if args and isinstance(args[0], dict):
            kwargs = {**args[0], **kwargs}
        method = self._resolve_method()
        result = method(**kwargs)
        if inspect.isawaitable(result):
            return await result
        return result

    def _resolve_method(self):
        """寻找测试桩上实际存在的工具方法，顺序覆盖当前项目所有适配器。"""
        for method_name in (
            "execute",
            "send",
            "extract",
            "fetch",
            "query",
            "prepare",
            "create",
            "list",
        ):
            method = getattr(self.legacy, method_name, None)
            if method is not None:
                return method
        raise AttributeError(f"legacy test tool has no supported async method: {self.name}")


def register_test_tool(
    registry: ToolRegistry,
    name: str,
    legacy: Any,
    *,
    capability: str | None = None,
) -> None:
    """按 ToolRegistry 的新协议注册测试桩。"""
    registry.register(
        LegacyTestTool(name=name, legacy=legacy),
        ToolSpec(name=name, capability=capability or f"tests.{name}"),
    )


def trusted_context(
    *,
    user_id: str = "test-user",
    event_id: str = "test-event",
    task_id: str = "test-task",
) -> ToolExecutionContext:
    """构造测试用受信上下文，减少单测里重复声明权限。"""
    return ToolExecutionContext(
        user_id=user_id,
        event_id=event_id,
        task_id=task_id,
        trusted_internal=True,
    )
