from __future__ import annotations

import asyncio

import pytest

from app.tools.base import ToolExecutionContext
from app.tools.registry import ToolRegistry
from app.tools.send_qq_message_tool import SendQqMessageTool
from tool_test_utils import register_test_tool


class FakeConnectorClient:
    """记录测试消息，不连接真实 NapCat。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def send_private_message(self, **kwargs):
        """模拟私聊发送并返回稳定成功结果。"""
        self.calls.append(kwargs)
        return {"status": "ok", "data": {"message_id": len(self.calls)}}

    async def send_group_message(self, **kwargs):
        """群聊测试复用相同记录逻辑。"""
        self.calls.append(kwargs)
        return {"status": "ok", "data": {"message_id": len(self.calls)}}


def test_should_reject_unauthorized_side_effect_tool() -> None:
    """没有显式授权时不能调用真实发送工具。"""

    async def scenario() -> None:
        registry = ToolRegistry()
        register_test_tool(registry, "send_qq_message", SendQqMessageTool(FakeConnectorClient()))
        context = ToolExecutionContext(user_id="u1", event_id="e1")

        with pytest.raises(PermissionError):
            await registry.ainvoke(
                "send_qq_message",
                context=context,
                idempotency_key="send:e1",
                arguments={
                    "chat_type": "private",
                    "chat_id": "10001",
                    "message": "你好",
                },
            )

    asyncio.run(scenario())


def test_should_send_qq_message_only_once_for_same_key() -> None:
    """相同幂等键重试时不会再次向 QQ 发送同一条消息。"""

    async def scenario() -> None:
        connector = FakeConnectorClient()
        registry = ToolRegistry()
        register_test_tool(registry, "send_qq_message", SendQqMessageTool(connector))
        context = ToolExecutionContext(
            user_id="u1",
            event_id="e1",
            task_id="task-1",
            allowed_tools=frozenset({"send_qq_message"}),
        )
        arguments = {
            "chat_type": "private",
            "chat_id": "10001",
            "message": "好的 那明晚见",
            "client_message_id": "send:e1:task-1:message:0",
            "correlation_id": "e1",
        }

        first = await registry.ainvoke(
            "send_qq_message",
            context=context,
            idempotency_key="send:e1:task-1",
            arguments=arguments,
        )
        second = await registry.ainvoke(
            "send_qq_message",
            context=context,
            idempotency_key="send:e1:task-1",
            arguments=arguments,
        )

        assert first == second
        assert len(connector.calls) == 1
        assert connector.calls[0]["client_message_id"] == "send:e1:task-1:message:0"

    asyncio.run(scenario())
