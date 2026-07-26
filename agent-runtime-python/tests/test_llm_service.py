from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

import httpx

from app.clients.llm_service import LlmHttpStatusError, LlmServiceClient
from app.tools.langchain_delegated_task_tools import delegated_task_action_tools


class LlmServiceClientTest(unittest.TestCase):

    def test_should_not_expose_legacy_tool_call_protocol(self) -> None:
        """工具只能由工作流中的 LangChain @tool 执行，客户端不得保留旧协议入口。"""
        self.assertFalse(hasattr(LlmServiceClient, "generate_tool_call"))
        self.assertFalse(hasattr(LlmServiceClient, "_extract_tool_call"))
    """验证模型供应商错误能够安全地穿过视觉解析链路。"""

    def test_should_convert_provider_error_without_leaking_image_or_key(self) -> None:
        """HTTP 错误应保留状态和错误码，同时清除 Base64 图片和疑似 API Key。"""
        client = LlmServiceClient(base_url="https://example.com/v1", api_key="secret", model="qwen3-vl-flash")
        request = httpx.Request("POST", "https://example.com/v1/chat/completions")
        response = httpx.Response(
            400,
            request=request,
            json={
                "error": {
                    "code": "InvalidParameter",
                    "message": "bad image data:image/png;base64,aGVsbG8= with sk-example-secret-value",
                }
            },
        )

        with self.assertRaises(LlmHttpStatusError) as raised:
            client._raise_for_status(
                response,
                "vision_analysis",
                {"model": "qwen3-vl-flash", "base_url": "https://example.com/v1"},
            )

        self.assertEqual(400, raised.exception.status_code)
        self.assertEqual("InvalidParameter", raised.exception.error_code)
        self.assertNotIn("aGVsbG8", raised.exception.detail)
        self.assertNotIn("sk-example", raised.exception.detail)

class LangChainToolContractTest(unittest.TestCase):
    """验证主控台动作只通过 LangChain @tool 的 schema 进入工作流。"""

    def test_should_validate_progress_arguments_through_langchain_tool(self) -> None:
        """不再解析模型自定义 JSON；由 @tool 统一校验并返回标准化意图。"""
        tools = {item.name: item for item in delegated_task_action_tools()}
        result = tools["update_delegated_task"].invoke(
            {
                "reason": "等待对方确认",
                "progressSummary": "已发出预约请求",
                "knownFacts": [],
                "pendingConditions": ["等待回复"],
                "evidence": [],
                "evidenceEventIds": [],
            }
        )

        self.assertEqual("update_delegated_task", result["intent"])
        self.assertEqual("已发出预约请求", result["arguments"]["progressSummary"])


class _TextResponse:
    """为普通 LangChain 文本调用测试提供最小响应对象。"""

    def __init__(self, content: str) -> None:
        # 这个构造函数的作用是保存模型返回的 JSON 工具计划正文。
        self.content = content


class _TextOnlyModel:
    """模拟只接受普通文本对话请求的模型。"""

    def __init__(self) -> None:
        # 这个构造函数的作用是记录调用次数，证明模型客户端只负责文本生成。
        self.invoke_calls = 0

    async def ainvoke(self, messages):  # noqa: ANN001 - 模拟 LangChain 接口
        """返回一个普通文本响应。"""
        self.invoke_calls += 1
        return _TextResponse("普通文本回复")


class LangChainTextCompatibilityTest(unittest.IsolatedAsyncioTestCase):
    """验证模型客户端只走普通 LangChain 消息调用。"""

    async def test_should_generate_plain_text_without_native_function_calling(self) -> None:
        """工具执行由工作流的 @tool 负责，客户端不再绑定原生 Function Calling。"""
        client = LlmServiceClient(
            base_url="https://example.com/v1",
            api_key="test-key",
            model="deepseek-v4-pro",
        )
        model = _TextOnlyModel()

        with patch.object(client, "_build_chat_model", return_value=model):
            result = await client.generate_reply("system", "user")

        self.assertEqual("普通文本回复", result)
        self.assertEqual(1, model.invoke_calls)


class SequencedAsyncClient:
    """按顺序抛出异常或返回响应，用于验证模型请求的瞬时故障重试。"""

    def __init__(self, outcomes: list[object]) -> None:
        # 这个构造函数的作用是保存每次 POST 应返回的结果，并统计实际调用次数。
        self.outcomes = list(outcomes)
        self.post_calls = 0

    def __call__(self, *args, **kwargs):
        """模拟 httpx.AsyncClient 构造函数，每次重试复用同一个测试客户端。"""
        return self

    async def __aenter__(self):
        """进入异步上下文并返回测试客户端本身。"""
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        """退出异步上下文；测试客户端没有真实连接需要关闭。"""
        return None

    async def post(self, *args, **kwargs) -> httpx.Response:
        """弹出下一个预置结果，异常会像真实 httpx 请求一样向上抛出。"""
        self.post_calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class LlmServiceRetryTest(unittest.IsolatedAsyncioTestCase):
    """验证模型客户端只对网络瞬时故障重试，不改变审批的闭锁语义。"""

    async def test_should_retry_once_after_read_timeout(self) -> None:
        """视觉请求首次读取超时时应自动重试，并返回第二次请求的正常文本。"""
        request = httpx.Request("POST", "https://example.com/v1/chat/completions")
        fake_client = SequencedAsyncClient([
            httpx.ReadTimeout("timed out", request=request),
            httpx.Response(
                200,
                request=request,
                json={"choices": [{"message": {"content": "重试成功"}}]},
            ),
        ])
        client = LlmServiceClient(
            base_url="https://example.com/v1",
            api_key="test-key",
            model="test-model",
            timeout_seconds=5,
        )

        with patch("app.clients.llm_service.httpx.AsyncClient", new=fake_client), patch(
            "app.clients.llm_service.asyncio.sleep", new=AsyncMock()
        ):
            # 文本与工具调用已经由 LangChain 负责协议层重试；视觉链路仍需手写
            # 多模态消息格式，因此继续覆盖本客户端的瞬时网络重试边界。
            result = await client.describe_image(
                "system",
                "data:image/png;base64,aGVsbG8=",
                "请描述图片",
            )

        self.assertEqual("重试成功", result)
        self.assertEqual(2, fake_client.post_calls)


if __name__ == "__main__":
    unittest.main()
