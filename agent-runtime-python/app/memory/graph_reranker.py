"""Graphiti CrossEncoder 本地实现。

Graphiti 构造时若未提供 cross_encoder，会默认创建 OpenAIRerankerClient
（无 api_key，直接报错）。P-A 阶段用 no-op 实现替代：检索结果保持
Graphiti 混合检索（语义+关键词+图遍历）的原始顺序，不依赖外部 rerank API。

后续 P-D 若需要中文重排序提升召回精度，可换成本地 BGE reranker
（graphiti_core.cross_encoder.bge_reranker_client.BGERerankerClient，
需 sentence-transformers + 模型下载）。
"""

from __future__ import annotations

from graphiti_core.cross_encoder.client import CrossEncoderClient


class NoOpRerankerClient(CrossEncoderClient):
    """保序重排序：原样返回候选，分数统一为 0.0。"""

    async def rank(self, query: str, passages: list[str]) -> list[tuple[str, float]]:
        return [(passage, 0.0) for passage in passages]
