from __future__ import annotations

import os
from typing import Any

import httpx

from app.schemas.model_profiles import ResolvedUserModelProfile


class LlmServiceClient:
    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout_seconds: float = 20.0,
    ) -> None:
        # 这个函数的作用是初始化一个 OpenAI 兼容协议的 LLM 客户端，便于后续切换不同模型提供方。
        self.base_url = (base_url or os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
        self.api_key = api_key or os.getenv("OPENAI_API_KEY") or ""
        self.model = model or os.getenv("OPENAI_MODEL") or "gpt-4o-mini"
        self.timeout_seconds = timeout_seconds

    def is_enabled(self, model_profile: ResolvedUserModelProfile | None = None) -> bool:
        # 这个函数的作用是判断当前运行时是否具备可调用大模型的最小配置，支持后端配置覆盖环境变量。
        merged_config = self._merge_runtime_config(model_profile)
        return bool(merged_config["api_key"].strip()) and bool(merged_config["model"].strip())

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

        payload: dict[str, Any] = {
            "model": merged_config["model"],
            "temperature": final_temperature,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
        }
        if merged_config["max_tokens"] is not None:
            payload["max_tokens"] = merged_config["max_tokens"]

        headers = {
            "Authorization": f"Bearer {merged_config['api_key']}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                f"{merged_config['base_url']}/chat/completions",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
            return self._extract_text(data)

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
