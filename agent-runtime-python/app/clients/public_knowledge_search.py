from __future__ import annotations

import asyncio
import os
import re
import time
from typing import Any

import httpx


class PublicKnowledgeSearchClient:
    """受限公共知识检索客户端，只接受已经脱敏的短实体查询。"""

    def __init__(
        self,
        api_key: str | None = None,
        endpoint: str | None = None,
        timeout_seconds: float = 8.0,
        cache_ttl_seconds: int = 86400,
    ) -> None:
        """读取 Tavily 配置并初始化短期缓存；未配置 Key 时客户端保持禁用。"""
        self.api_key = (api_key or os.getenv("TAVILY_API_KEY") or "").strip()
        self.endpoint = (
            endpoint
            or os.getenv("TAVILY_SEARCH_ENDPOINT")
            or "https://api.tavily.com/search"
        ).strip()
        self.timeout_seconds = min(max(float(timeout_seconds), 2.0), 20.0)
        self.cache_ttl_seconds = max(int(cache_ttl_seconds), 60)
        self._cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}

    def is_enabled(self) -> bool:
        """只有显式配置搜索 API Key 后才允许访问公共网络。"""
        return bool(self.api_key and self.endpoint)

    async def search(
        self,
        queries: list[str],
        max_queries: int = 2,
        max_results_per_query: int = 2,
    ) -> list[dict[str, Any]]:
        """
        并发搜索少量实体问题，并返回截断后的标题、链接和摘要。

        调用方不能传入完整聊天原文；本方法还会二次删除 URL、邮箱、手机号和长数字，
        避免模型误把私聊中的身份信息拼进外部搜索请求。
        """
        if not self.is_enabled():
            return []

        normalized_queries: list[str] = []
        for query in queries:
            sanitized = self.sanitize_query(query)
            if sanitized and sanitized not in normalized_queries:
                normalized_queries.append(sanitized)
            if len(normalized_queries) >= min(max(max_queries, 1), 2):
                break
        if not normalized_queries:
            return []

        batches = await asyncio.gather(
            *(
                self._search_one(query, min(max(max_results_per_query, 1), 3))
                for query in normalized_queries
            ),
            return_exceptions=True,
        )
        merged: list[dict[str, Any]] = []
        for batch in batches:
            if isinstance(batch, Exception):
                continue
            for item in batch:
                dedupe_key = (str(item.get("url", "")), str(item.get("content", "")))
                if item and not any(
                    (str(existing.get("url", "")), str(existing.get("content", ""))) == dedupe_key
                    for existing in merged
                ):
                    merged.append(item)
        return merged[:4]

    async def _search_one(self, query: str, max_results: int) -> list[dict[str, Any]]:
        """执行单个 Tavily Basic Search，并把原始响应收敛成内部稳定结构。"""
        now = time.monotonic()
        cached = self._cache.get(query)
        if cached and now - cached[0] <= self.cache_ttl_seconds:
            return [dict(item) for item in cached[1]]

        payload = {
            "query": query,
            "search_depth": "basic",
            "max_results": max_results,
            "include_answer": False,
            "include_raw_content": False,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(self.endpoint, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()

        normalized: list[dict[str, Any]] = []
        for item in data.get("results") or []:
            if not isinstance(item, dict):
                continue
            title = self._compact_text(item.get("title"), 120)
            content = self._compact_text(item.get("content"), 500)
            url = str(item.get("url") or "").strip()[:500]
            if not content:
                continue
            normalized.append(
                {
                    "source": "public_web_search",
                    "query": query,
                    "title": title,
                    "url": url,
                    "content": content,
                    "score": item.get("score"),
                }
            )
        self._cache[query] = (now, normalized)
        return [dict(item) for item in normalized]

    @staticmethod
    def sanitize_query(query: str) -> str:
        """把模型建议的查询压缩为不含联系方式和完整聊天片段的实体级短查询。"""
        text = " ".join(str(query or "").split())
        text = re.sub(r"https?://\S+|www\.\S+", " ", text, flags=re.IGNORECASE)
        text = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", " ", text)
        text = re.sub(r"(?<!\d)1[3-9]\d{9}(?!\d)", " ", text)
        text = re.sub(r"(?<!\d)\d{5,}(?!\d)", " ", text)
        text = re.sub(r"[\r\n\t]+", " ", text)
        text = re.sub(r"\s{2,}", " ", text).strip(" ,，。;；:：\"'[]{}")
        # 公共检索只需要实体和冲突关系；过长文本通常意味着模型复制了私聊原文。
        return text[:60].strip()

    @staticmethod
    def _compact_text(value: Any, limit: int) -> str:
        """压平并截断搜索摘要，避免把整页内容塞回上下文。"""
        return " ".join(str(value or "").split())[:limit].strip()
