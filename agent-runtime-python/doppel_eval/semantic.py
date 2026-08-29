"""Domain-neutral local semantic retrieval used by the synthetic quality suite."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Sequence
from typing import Any


class FastEmbedEvaluationEncoder:
    """One lazily loaded local embedding model shared across E2E scenes."""

    def __init__(self, model_name: str = "BAAI/bge-small-zh-v1.5") -> None:
        self.model_name = str(model_name).strip()
        if not self.model_name:
            raise ValueError("embedding model name is required")
        self._model: Any | None = None
        self._lock = asyncio.Lock()

    async def similarities(self, query: str, documents: Sequence[str]) -> list[float]:
        if not documents:
            return []
        model = await self._get_model()
        query_vector, document_vectors = await asyncio.gather(
            asyncio.to_thread(lambda: next(iter(model.query_embed([query])))),
            asyncio.to_thread(lambda: list(model.embed(list(documents)))),
        )
        return [_cosine(query_vector, vector) for vector in document_vectors]

    async def _get_model(self) -> Any:
        if self._model is not None:
            return self._model
        async with self._lock:
            if self._model is None:
                from fastembed import TextEmbedding

                self._model = await asyncio.to_thread(
                    TextEmbedding,
                    model_name=self.model_name,
                )
        return self._model


class FastEmbedEvaluationSemanticIndex:
    """Brute-force quality oracle over one temporary E2E Store.

    This deliberately has no domain dictionaries and is labelled evaluation-only:
    it measures semantic retrieval quality, not production index throughput.
    """

    def __init__(
        self,
        dm: Any,
        store: Any,
        encoder: FastEmbedEvaluationEncoder,
        *,
        max_records_per_scope: int = 5_000,
    ) -> None:
        self._dm = dm
        self._store = store
        self._encoder = encoder
        self._max_records_per_scope = max_records_per_scope

    async def search(
        self,
        query: str,
        scopes: Sequence[Any],
        *,
        filters: Any | None = None,
        limit: int = 10,
    ) -> list[Any]:
        if limit <= 0:
            return []
        records: list[Any] = []
        for scope in scopes:
            records.extend(await self._read_scope(scope, filters))
        scores = await self._encoder.similarities(
            query, [record.content for record in records]
        )
        ranked = sorted(
            zip(records, scores, strict=True),
            key=lambda item: (item[1], item[0].memory_id),
            reverse=True,
        )
        return [
            self._dm.RecallResult(
                fact=record.content,
                kind=record.kind,
                scope=record.scope,
                memory_id=record.memory_id,
                actor=record.actor,
                authority=record.authority,
                source_event_id=record.source_event_id,
                source_message_id=record.source_message_id,
                extractor=record.extractor,
                extracted_at=record.updated_at,
                similarity=score,
                state=record.state,
            )
            for record, score in ranked[:limit]
        ]

    async def _read_scope(self, scope: Any, filters: Any | None) -> list[Any]:
        records: list[Any] = []
        cursor = ""
        while True:
            remaining = self._max_records_per_scope - len(records)
            if remaining <= 0:
                raise RuntimeError(
                    "evaluation semantic index exceeded max_records_per_scope"
                )
            page = await self._store.scan(
                scope,
                filters=filters,
                cursor=cursor,
                limit=min(500, remaining),
            )
            records.extend(page.records)
            if not page.has_more:
                return records
            cursor = page.next_cursor


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or len(left) == 0:
        raise ValueError("embedding vectors must have equal non-zero dimensions")
    dot = sum(float(a) * float(b) for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(float(value) ** 2 for value in left))
    right_norm = math.sqrt(sum(float(value) ** 2 for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return min(max(dot / (left_norm * right_norm), 0.0), 1.0)
