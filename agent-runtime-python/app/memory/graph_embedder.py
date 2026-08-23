"""Graphiti 自定义 Embedder：复用项目已有的本地向量服务。

Graphiti 的 EmbedderClient 抽象接口只要求实现 ``create``（单条）与
``create_batch``（批量）。我们把 ``EmbeddingServiceClient``（默认本地
BAAI/bge-small-zh-v1.5 ONNX，可被远程 OpenAI 兼容服务覆盖）包装为
Graphiti 可用的 embedder，不引入第二套向量链路。

维度说明：bge-small-zh-v1.5 输出 512 维，与 Graphiti 默认 1024 不一致，
这里通过 ``EmbedderConfig(embedding_dim=512)`` 显式声明。
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from graphiti_core.embedder.client import EmbedderClient, EmbedderConfig

logger = logging.getLogger(__name__)

LOCAL_EMBEDDING_DIM = 512


class RuntimeEmbedderClient(EmbedderClient):
    """把 EmbeddingServiceClient 包装为 Graphiti 的 EmbedderClient。"""

    def __init__(self, embedding_service: Any, config: EmbedderConfig | None = None) -> None:
        # 这个构造函数的作用是保存底层向量服务并声明向量维度；维度必须与实际模型输出一致。
        self._embedding_service = embedding_service
        self.config = config or EmbedderConfig(embedding_dim=LOCAL_EMBEDDING_DIM)

    def _normalize_input(self, input_data: str | list[str] | Iterable[int] | Iterable[Iterable[int]]) -> list[str]:
        # 这个函数的作用是把 Graphiti 可能传入的多种输入形态统一为文本列表；
        # Graphiti 只传文本，若出现整数序列（token ids）则属于不支持的后门路径，直接报错。
        if isinstance(input_data, str):
            return [input_data]
        if isinstance(input_data, list) and all(isinstance(item, str) for item in input_data):
            return input_data
        raise TypeError(f"RuntimeEmbedderClient 只接受文本输入，收到: {type(input_data)}")

    async def create(self, input_data: str | list[str] | Iterable[int] | Iterable[Iterable[int]]) -> list[float]:
        texts = self._normalize_input(input_data)
        vectors = await self._embedding_service.embed(texts)
        return list(vectors[0])

    async def create_batch(self, input_data_list: list[str]) -> list[list[float]]:
        return await self._embedding_service.embed(input_data_list)
