"""Graphiti 时间感知记忆图谱服务（P-A 基础设施）。

职责：
- 懒初始化 Graphiti（Neo4j + 本地 embedder + DeepSeek LLM client）。
- 提供 ``write_episode`` / ``search`` / ``close`` 三个最小入口，后续 P-B/P-C
  的事件接入、检索注入都在此服务之上扩展。
- 降级：未启用（MEMORY_GRAPH_ENABLED != true）或 Neo4j/LLM 异常时，
  写入返回 None、检索返回空列表，绝不阻断主消息链路。

LLM 配置解析顺序（不落日志、不落库）：
1. 环境变量 OPENAI_API_KEY / OPENAI_BASE_URL / OPENAI_MODEL；
2. 后端 Event Center ``resolve_user_model_profile``（用户模型配置，解密后明文）。
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import datetime
from typing import Any

from graphiti_core import Graphiti
from graphiti_core.embedder.client import EmbedderClient
from graphiti_core.llm_client import OpenAIClient
from graphiti_core.llm_client.config import LLMConfig

from app.clients.embedding_service import EmbeddingServiceClient
from app.clients.event_center_service import EventCenterServiceClient
from app.memory.graph_embedder import RuntimeEmbedderClient
from app.memory.graph_reranker import NoOpRerankerClient

logger = logging.getLogger(__name__)

DEFAULT_MODEL_ROUTE = "message_dispatch"


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


class MemoryGraphService:
    """Graphiti 记忆图谱的进程内门面；所有方法在失败时按降级策略返回安全空值。"""

    def __init__(
        self,
        event_center_client: EventCenterServiceClient | None = None,
        embedding_service: EmbeddingServiceClient | None = None,
        neo4j_uri: str | None = None,
        neo4j_user: str | None = None,
        neo4j_password: str | None = None,
        enabled: bool | None = None,
        user_id: str | None = None,
        model_route: str = DEFAULT_MODEL_ROUTE,
    ) -> None:
        # 这个构造函数的作用是固化配置并准备懒初始化的锁；真正的 Neo4j 连接推迟到首次写入/检索。
        self._neo4j_uri = (neo4j_uri or os.getenv("NEO4J_URI", "bolt://127.0.0.1:7687")).strip()
        self._neo4j_user = (neo4j_user or os.getenv("NEO4J_USER", "neo4j")).strip()
        self._neo4j_password = neo4j_password or os.getenv("NEO4J_PASSWORD", "memoecho2026")
        self._enabled = enabled if enabled is not None else _env_bool("MEMORY_GRAPH_ENABLED", False)
        self._user_id = (user_id or os.getenv("MEMO_ECHO_RUNTIME_USER_ID", "")).strip()
        self._model_route = model_route.strip()
        self._event_center_client = event_center_client
        self._embedding_service = embedding_service or EmbeddingServiceClient()
        self._graphiti: Graphiti | None = None
        self._lock = asyncio.Lock()

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    @staticmethod
    def normalize_group_id(raw: str | None) -> str | None:
        """把 scope 字符串规范化为 Graphiti 允许的 group_id（字母数字/短横线/下划线）。"""
        if not raw:
            return raw
        return re.sub(r"[^a-zA-Z0-9_-]", "_", raw.strip())

    async def write_episode(
        self,
        *,
        name: str,
        episode_body: str,
        source_description: str,
        reference_time: datetime,
        group_id: str | None = None,
        uuid: str | None = None,
        source: Any = None,
    ) -> Any | None:
        """写入一条事件 Episode；失败降级返回 None。"""
        if not self._enabled:
            return None
        try:
            graphiti = await self._ensure_graphiti()
            kwargs: dict[str, Any] = {
                "name": name,
                "episode_body": episode_body,
                "source_description": source_description,
                "reference_time": reference_time,
                "group_id": self.normalize_group_id(group_id),
            }
            if uuid:
                kwargs["uuid"] = uuid
            if source is not None:
                kwargs["source"] = source
            return await graphiti.add_episode(**kwargs)
        except Exception as exc:  # noqa: BLE001 - 降级必须吞掉一切异常
            logger.warning("memory graph write_episode degraded: %s", exc)
            return None

    async def search(
        self,
        query: str,
        group_ids: list[str] | None = None,
        num_results: int = 10,
    ) -> list[Any]:
        """语义 + 关键词 + 图遍历混合检索；失败降级返回空列表。"""
        if not self._enabled:
            return []
        try:
            graphiti = await self._ensure_graphiti()
            normalized = [self.normalize_group_id(g) for g in (group_ids or [])]
            return await graphiti.search(
                query=query,
                group_ids=normalized,
                num_results=num_results,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("memory graph search degraded: %s", exc)
            return []

    async def close(self) -> None:
        if self._graphiti is not None:
            try:
                await self._graphiti.close()
            except Exception:  # noqa: BLE001
                logger.debug("memory graph close failed", exc_info=True)
            self._graphiti = None

    async def _ensure_graphiti(self) -> Graphiti:
        if self._graphiti is not None:
            return self._graphiti
        async with self._lock:
            if self._graphiti is None:
                self._graphiti = await self._build_graphiti()
        return self._graphiti

    async def _build_graphiti(self) -> Graphiti:
        llm_client = await self._resolve_llm_client()
        embedder: EmbedderClient = RuntimeEmbedderClient(self._embedding_service)
        graphiti = Graphiti(
            uri=self._neo4j_uri,
            user=self._neo4j_user,
            password=self._neo4j_password,
            llm_client=llm_client,
            embedder=embedder,
            cross_encoder=NoOpRerankerClient(),
        )
        logger.info(
            "memory graph initialized: uri=%s user=%s llm=%s embedder=local(bge-small-zh)",
            self._neo4j_uri,
            self._neo4j_user,
            self._describe_llm(),
        )
        return graphiti

    def _describe_llm(self) -> str:
        # 这个函数的作用是提供不泄露密钥的 LLM 标识，供日志与诊断。
        model = os.getenv("OPENAI_MODEL", "").strip()
        return f"env:{model}" if model else "backend-profile"

    async def _resolve_llm_client(self) -> OpenAIClient:
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        base_url = os.getenv("OPENAI_BASE_URL", "").strip()
        model = os.getenv("OPENAI_MODEL", "").strip()

        if not api_key and self._event_center_client is not None:
            try:
                resolved = await self._event_center_client.resolve_user_model_profile(
                    self._model_route,
                    user_id=self._user_id,
                )
                profile = resolved.profile if resolved.matched else None
                if profile is not None and profile.api_key.strip():
                    api_key = profile.api_key.strip()
                    base_url = profile.base_url.strip() or base_url
                    model = profile.model.strip() or model
            except Exception as exc:  # noqa: BLE001
                logger.warning("memory graph model profile resolve failed: %s", exc)

        if not api_key:
            raise RuntimeError(
                "Memory graph LLM is not configured: set OPENAI_API_KEY or provide event_center_client with a resolvable model profile"
            )
        return OpenAIClient(LLMConfig(api_key=api_key, model=model or None, base_url=base_url or None))
