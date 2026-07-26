from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any

import httpx
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI

from app.schemas.model_profiles import ResolvedUserModelProfile


logger = logging.getLogger(__name__)


class LlmHttpStatusError(RuntimeError):
    """保存模型供应商返回的安全错误摘要，避免上层只看到笼统的 HTTPStatusError。"""

    def __init__(self, status_code: int, error_code: str, detail: str) -> None:
        # 这个构造函数的作用是保留可展示的状态码、错误码和脱敏错误说明。
        self.status_code = status_code
        self.error_code = error_code
        self.detail = detail
        message = f"HTTP {status_code}"
        if error_code:
            message += f" {error_code}"
        if detail:
            message += f": {detail}"
        super().__init__(message)


class LlmServiceClient:
    _TRANSIENT_EXCEPTIONS = (
        httpx.ReadTimeout,
        httpx.ConnectTimeout,
        httpx.ConnectError,
        httpx.RemoteProtocolError,
    )

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        # 这个函数的作用是初始化一个 OpenAI 兼容协议的 LLM 客户端，便于后续切换不同模型提供方。
        self.base_url = (base_url or os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
        self.api_key = api_key or os.getenv("OPENAI_API_KEY") or ""
        self.model = model or os.getenv("OPENAI_MODEL") or "gpt-4o-mini"
        self.timeout_seconds = self._resolve_timeout_seconds(timeout_seconds)

    @staticmethod
    def _resolve_timeout_seconds(configured_timeout: float | None) -> float:
        """解析模型请求超时；私聊上下文模型可能较慢，因此默认留出 45 秒。"""
        raw_timeout = configured_timeout if configured_timeout is not None else os.getenv("LLM_REQUEST_TIMEOUT_SECONDS", "45")
        try:
            return min(max(float(raw_timeout), 5.0), 120.0)
        except (TypeError, ValueError):
            return 45.0

    def is_enabled(self, model_profile: ResolvedUserModelProfile | None = None) -> bool:
        # 这个函数的作用是判断当前运行时是否具备可调用大模型的最小配置，支持后端配置覆盖环境变量。
        merged_config = self._merge_runtime_config(model_profile)
        return bool(merged_config["api_key"].strip()) and bool(merged_config["model"].strip())

    @classmethod
    def is_transient_error(cls, exception: BaseException) -> bool:
        """判断异常是否属于可重试的短暂网络故障，供上层生成准确的接管原因。"""
        return isinstance(exception, cls._TRANSIENT_EXCEPTIONS)

    async def generate_reply(
        self,
        system_prompt: str,
        user_message: str,
        temperature: float = 0.7,
        model_profile: ResolvedUserModelProfile | None = None,
    ) -> str:
        # 这个函数的作用是把系统提示词和用户消息发给大模型，并按用户配置覆盖默认模型参数。
        merged_config = self._merge_runtime_config(model_profile)
        final_temperature = temperature if model_profile is None or model_profile.temperature is None else model_profile.temperature

        if not self.is_enabled(model_profile):
            raise RuntimeError("LLM client is not configured")

        # LangChain 统一负责 OpenAI 兼容模型的消息序列化和响应解析，避免不同厂商
        # 在 content 格式、工具调用结构上的细微差异扩散到各个 Agent。
        model = self._build_chat_model(
            merged_config=merged_config,
            temperature=final_temperature,
        )
        response = await model.ainvoke(
            [
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_message),
            ]
        )
        return self._message_text(response)

    async def choose_tool(
        self,
        system_prompt: str,
        user_message: str,
        tools: list[BaseTool],
        temperature: float = 0.1,
        model_profile: ResolvedUserModelProfile | None = None,
    ) -> dict[str, Any] | None:
        """让支持工具调用的模型直接选择 LangChain @tool。

        这里只负责拿到模型声明的工具名和参数，不在客户端层执行工具。
        如果当前模型或供应商不支持 tool calling，则返回 None 交给上层 JSON 兼容兜底。
        """
        if not tools:
            return None

        merged_config = self._merge_runtime_config(model_profile)
        final_temperature = temperature if model_profile is None or model_profile.temperature is None else model_profile.temperature

        if not self.is_enabled(model_profile):
            raise RuntimeError("LLM client is not configured")

        if self._should_skip_native_tool_calling(merged_config):
            logger.info(
                "当前模型不走原生 tool calling，直接交给 LangGraph JSON 工具规划。model=%s, baseUrl=%s",
                merged_config.get("model") or "",
                merged_config.get("base_url") or "",
            )
            return None

        model = self._build_chat_model(
            merged_config=merged_config,
            temperature=final_temperature,
        )
        tool_bound_model = model.bind_tools(tools)
        try:
            response = await tool_bound_model.ainvoke(
                [
                    SystemMessage(content=system_prompt),
                    HumanMessage(content=user_message),
                ]
            )
        except Exception as exception:
            detail = str(exception)
            lowered = detail.lower()
            if any(keyword in lowered for keyword in ("tool", "function", "tool_choice", "unsupported")):
                logger.warning(
                    "模型原生工具调用不可用，回退到 JSON 兼容规划：model=%s, error=%s",
                    merged_config.get("model") or "",
                    detail[:240],
                )
                return None
            raise

        tool_calls = getattr(response, "tool_calls", None) or []
        if not tool_calls:
            return None

        first_call = tool_calls[0]
        if not isinstance(first_call, dict):
            return None

        name = str(first_call.get("name") or "").strip()
        arguments = first_call.get("args") or first_call.get("arguments") or {}
        if not isinstance(arguments, dict):
            arguments = {}
        if not name:
            return None
        return {
            "name": name,
            "arguments": arguments,
            "raw": self._message_text_or_empty(response),
        }

    def _build_chat_model(
        self,
        merged_config: dict[str, Any],
        temperature: float,
    ) -> ChatOpenAI:
        """按当前用户模型配置创建 LangChain ChatOpenAI 实例。

        该函数只适配模型协议，不保存会话状态。持久化记忆、权限判断和工具副作用仍然
        由 LangGraph 与 Java 事件中心负责，避免模型客户端越权。
        """
        parameters: dict[str, Any] = {
            "model": merged_config["model"],
            "api_key": merged_config["api_key"],
            "base_url": merged_config["base_url"],
            "temperature": temperature,
            "timeout": self.timeout_seconds,
            # 网络故障由 LangChain 在本次请求内重试一次；业务 4xx 不会被重试。
            "max_retries": 1,
        }
        if merged_config["max_tokens"] is not None:
            parameters["max_tokens"] = merged_config["max_tokens"]
        return ChatOpenAI(**parameters)

    @staticmethod
    def _should_skip_native_tool_calling(merged_config: dict[str, Any]) -> bool:
        """判断当前模型是否应跳过 LangChain 原生工具绑定。

        DeepSeek 官方接口在部分思考模式下会拒绝 tool_choice。这里直接返回 None，
        让上层 LangGraph 使用同一批 @tool 的 JSON 规划兜底，避免每轮都先触发一次 400。
        """
        model = str(merged_config.get("model") or "").lower()
        base_url = str(merged_config.get("base_url") or "").lower()
        return "deepseek" in model and "api.deepseek.com" in base_url

    @staticmethod
    def _message_text(response: Any) -> str:
        """从 LangChain AIMessage 提取纯文本，同时兼容供应商返回的分段内容。"""
        content = getattr(response, "content", "")
        if isinstance(content, str):
            text = content.strip()
            if text:
                return text
        if isinstance(content, list):
            fragments: list[str] = []
            for item in content:
                if isinstance(item, str):
                    fragments.append(item)
                elif isinstance(item, dict) and item.get("text"):
                    fragments.append(str(item["text"]))
            text = "".join(fragments).strip()
            if text:
                return text
        raise RuntimeError("LLM response does not contain text content")

    @classmethod
    def _message_text_or_empty(cls, response: Any) -> str:
        """尽力提取模型正文；工具调用响应没有正文时返回空字符串。"""
        try:
            return cls._message_text(response)
        except RuntimeError:
            return ""

    async def describe_image(
        self,
        system_prompt: str,
        image_data_url: str,
        user_message: str = "",
        model_profile: ResolvedUserModelProfile | None = None,
    ) -> str:
        """使用 OpenAI 兼容视觉消息理解图片，只返回可由图片直接观察到的事实。"""
        merged_config = self._merge_runtime_config(model_profile)
        if not self.is_enabled(model_profile):
            raise RuntimeError("LLM client is not configured")

        content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": user_message or "请识别这张图片中与当前聊天有关的可见信息。",
            },
            {
                "type": "image_url",
                "image_url": {"url": image_data_url},
            },
        ]
        payload: dict[str, Any] = {
            "model": merged_config["model"],
            "temperature": 0.1,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content},
            ],
        }
        if merged_config["max_tokens"] is not None:
            payload["max_tokens"] = merged_config["max_tokens"]

        headers = {
            "Authorization": f"Bearer {merged_config['api_key']}",
            "Content-Type": "application/json",
        }
        response = await self._post_chat_completion(
            headers=headers,
            payload=payload,
            operation="vision_analysis",
            merged_config=merged_config,
        )
        return self._extract_text(response.json())

    async def _post_chat_completion(
        self,
        headers: dict[str, str],
        payload: dict[str, Any],
        operation: str,
        merged_config: dict[str, Any],
    ) -> httpx.Response:
        """发送模型请求；只对网络瞬时故障重试一次，不重试供应商业务错误。"""
        max_attempts = 2
        for attempt in range(1, max_attempts + 1):
            try:
                async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                    response = await client.post(
                        f"{merged_config['base_url']}/chat/completions",
                        headers=headers,
                        json=payload,
                    )
                self._raise_for_status(response, operation, merged_config)
                return response
            except self._TRANSIENT_EXCEPTIONS as exception:
                if attempt >= max_attempts:
                    raise
                logger.warning(
                    "模型接口发生瞬时网络异常，准备重试：operation=%s, model=%s, attempt=%d, error=%s",
                    operation,
                    merged_config.get("model") or "",
                    attempt,
                    type(exception).__name__,
                )
                await asyncio.sleep(0.35)

        raise RuntimeError("模型请求未执行")

    def _raise_for_status(
        self,
        response: httpx.Response,
        operation: str,
        merged_config: dict[str, Any],
    ) -> None:
        """把供应商 HTTP 错误转换成脱敏异常，并记录不含 API Key 和图片 Base64 的诊断信息。"""
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exception:
            error_code, detail = self._extract_provider_error(response)
            logger.warning(
                "模型接口调用失败：operation=%s, status=%s, model=%s, baseUrl=%s, code=%s, detail=%s",
                operation,
                response.status_code,
                merged_config.get("model") or "",
                merged_config.get("base_url") or "",
                error_code or "unknown",
                detail or "未返回错误说明",
            )
            raise LlmHttpStatusError(response.status_code, error_code, detail) from exception

    @classmethod
    def _extract_provider_error(cls, response: httpx.Response) -> tuple[str, str]:
        """兼容 OpenAI、DashScope 等常见错误结构，并删除可能回显的密钥和 Base64 数据。"""
        error_code = ""
        detail = ""
        try:
            payload = response.json()
        except ValueError:
            payload = None

        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict):
                error_code = str(error.get("code") or error.get("type") or "").strip()
                detail = str(error.get("message") or "").strip()
            else:
                error_code = str(payload.get("code") or "").strip()
                detail = str(payload.get("message") or error or "").strip()
        if not detail:
            detail = response.text.strip()

        compact_detail = " ".join(detail.split())
        compact_detail = re.sub(r"data:image/[^;]+;base64,[A-Za-z0-9+/=]+", "<image-data>", compact_detail)
        compact_detail = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}\b", "<api-key>", compact_detail)
        return error_code[:80], compact_detail[:300]

    def _merge_runtime_config(self, model_profile: ResolvedUserModelProfile | None = None) -> dict[str, Any]:
        # 这个函数的作用是把环境变量默认模型配置与后端返回的用户模型配置合并成最终请求参数。
        merged_base_url = self.base_url
        merged_api_key = self.api_key
        merged_model = self.model
        merged_max_tokens: int | None = None

        if model_profile is not None:
            if model_profile.base_url.strip():
                merged_base_url = model_profile.base_url.strip().rstrip("/")
            if model_profile.api_key.strip():
                merged_api_key = model_profile.api_key.strip()
            if model_profile.model.strip():
                merged_model = model_profile.model.strip()
            merged_max_tokens = model_profile.max_tokens

        return {
            "base_url": merged_base_url,
            "api_key": merged_api_key,
            "model": merged_model,
            "max_tokens": merged_max_tokens,
        }

    def _extract_text(self, data: dict[str, Any]) -> str:
        # 这个函数的作用是从 OpenAI 兼容返回结构里提取首条文本内容，并兼容字符串和分段列表两种格式。
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError("LLM response does not contain choices")

        message = choices[0].get("message") or {}
        content = message.get("content")

        if isinstance(content, str):
            return content.strip()

        if isinstance(content, list):
            texts = []
            for item in content:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "text" and item.get("text"):
                    texts.append(str(item["text"]))
            joined_text = "".join(texts).strip()
            if joined_text:
                return joined_text

        raise RuntimeError("LLM response does not contain readable text")
