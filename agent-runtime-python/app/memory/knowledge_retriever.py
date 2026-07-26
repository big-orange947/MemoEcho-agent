from __future__ import annotations

import asyncio
import html
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import httpx

from app.schemas.events import UnifiedEvent
from app.schemas.profiles import ConversationProfile


class KnowledgeRetriever:
    """从会话设定绑定的资料中提取少量相关片段，避免把整份外部资料塞入模型上下文。"""

    _CACHE_TTL_SECONDS = 300
    _MAX_SOURCES = 8
    _MAX_SOURCE_CHARS = 120_000
    _MAX_FRAGMENT_CHARS = 1_000
    _MAX_RESULTS = 3

    def __init__(self) -> None:
        # 缓存只保存已清洗的公开资料，避免每一条聊天消息都重新下载同一个知识库页面。
        self._cache: dict[str, tuple[float, str]] = {}

    async def retrieve(self, event: UnifiedEvent, profile: ConversationProfile) -> list[dict[str, Any]]:
        """按当前消息检索配置来源，失败的单个来源只跳过，不阻塞主对话链路。"""
        sources = [source.strip() for source in profile.knowledge_base_sources if source and source.strip()]
        if not sources:
            return []

        documents = await asyncio.gather(
            *(self._load_source(source) for source in sources[: self._MAX_SOURCES]),
            return_exceptions=True,
        )
        query_tokens = self._query_tokens(event.text or "")
        results: list[dict[str, Any]] = []
        for source, document in zip(sources, documents, strict=False):
            if isinstance(document, Exception) or not document:
                continue
            fragment, score = self._select_fragment(document, query_tokens)
            if fragment:
                results.append({"source": source, "content": fragment, "score": score})

        results.sort(key=lambda item: item["score"], reverse=True)
        return results[: self._MAX_RESULTS]

    async def _load_source(self, source: str) -> str:
        """读取本地纯文本文件或公开 HTTP(S) 页面，并执行大小与缓存限制。"""
        cached = self._cache.get(source)
        if cached and time.monotonic() - cached[0] < self._CACHE_TTL_SECONDS:
            return cached[1]

        parsed = urlparse(source)
        if parsed.scheme in {"http", "https"}:
            content = await self._fetch_http_source(source)
        else:
            content = await asyncio.to_thread(self._read_local_source, source)

        cleaned = self._clean_document(content)[: self._MAX_SOURCE_CHARS]
        self._cache[source] = (time.monotonic(), cleaned)
        return cleaned

    async def _fetch_http_source(self, source: str) -> str:
        """下载用户显式配置的公开资料，不携带 Cookie、Token 或本地服务访问权限。"""
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
            response = await client.get(source, headers={"User-Agent": "MemoEchoKnowledgeRetriever/0.1"})
            response.raise_for_status()
            return response.text

    def _read_local_source(self, source: str) -> str:
        """读取用户明确选择的本地文本资料；二进制格式须先用文件解析能力转换为文本。"""
        path_text = unquote(urlparse(source).path) if source.startswith("file://") else source
        path = Path(path_text)
        if not path.is_file():
            return ""
        if path.suffix.lower() not in {".txt", ".md", ".markdown", ".json", ".csv", ".log"}:
            return ""
        return path.read_text(encoding="utf-8", errors="ignore")

    @staticmethod
    def _clean_document(content: str) -> str:
        """移除网页标签和多余空白，并把资料视为数据而非可执行指令。"""
        text = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", content, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
        text = html.unescape(text)
        text = re.sub(r"[\t\r\f\v ]+", " ", text)
        # 保留段落边界，后续检索才能挑出相关片段而不是把整篇资料作为一段。
        return re.sub(r"\n\s*\n+", "\n\n", text).strip()

    def _select_fragment(self, document: str, query_tokens: set[str]) -> tuple[str, int]:
        """在段落边界挑选和当前消息重合度最高的片段；没有命中时仅返回文档开头摘要。"""
        paragraphs = [part.strip() for part in re.split(r"(?:\r?\n){2,}|(?<=[。！？.!?])\s+", document) if part.strip()]
        if not paragraphs:
            return "", 0
        scored = [
            (sum(token in paragraph.lower() for token in query_tokens), index, paragraph)
            for index, paragraph in enumerate(paragraphs)
        ]
        score, _, paragraph = max(scored, key=lambda item: (item[0], -item[1]))
        return paragraph[: self._MAX_FRAGMENT_CHARS], score

    @staticmethod
    def _query_tokens(text: str) -> set[str]:
        """提取中英文检索词；中文使用双字片段，降低单个常见汉字造成的误命中。"""
        normalized = text.lower()
        tokens = set(re.findall(r"[a-z0-9_]{2,}", normalized))
        chinese = "".join(re.findall(r"[\u4e00-\u9fff]", normalized))
        tokens.update(chinese[index:index + 2] for index in range(max(0, len(chinese) - 1)))
        return {token for token in tokens if token}
