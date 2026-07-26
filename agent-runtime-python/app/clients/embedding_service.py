from __future__ import annotations

import asyncio
import math
import os
from pathlib import Path
from typing import Any, Callable

import httpx


LocalModelFactory = Callable[[str, str, int], Any]


class EmbeddingServiceClient:
    """默认使用本地 ONNX 向量模型，也允许完整的远程配置覆盖本地后端。"""

    DEFAULT_LOCAL_MODEL = "BAAI/bge-small-zh-v1.5"

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout_seconds: float | None = None,
        local_model_name: str | None = None,
        local_cache_dir: str | None = None,
        local_threads: int | None = None,
        local_model_factory: LocalModelFactory | None = None,
    ) -> None:
        # 这个构造函数的作用是同时准备可选的远程后端和零配置本地后端；远程配置不完整时自动使用本地模型。
        self.base_url = (base_url if base_url is not None else os.getenv("EMBEDDING_BASE_URL", "")).rstrip("/")
        self.api_key = api_key if api_key is not None else os.getenv("EMBEDDING_API_KEY", "")
        self.model = model if model is not None else os.getenv("EMBEDDING_MODEL", "")
        self.timeout_seconds = self._resolve_timeout_seconds(timeout_seconds)
        self.local_model_name = (local_model_name or self.DEFAULT_LOCAL_MODEL).strip()
        self.local_cache_dir = self._resolve_local_cache_dir(local_cache_dir)
        self.local_threads = self._resolve_local_threads(local_threads)
        self._local_model_factory = local_model_factory
        self._local_model: Any | None = None
        self._local_model_lock = asyncio.Lock()

    @staticmethod
    def _resolve_timeout_seconds(configured_timeout: float | None) -> float:
        # 这个函数的作用是限制远程向量请求的超时时间，防止高级覆盖配置长期阻塞消息主链路。
        raw_timeout = configured_timeout if configured_timeout is not None else os.getenv(
            "EMBEDDING_REQUEST_TIMEOUT_SECONDS",
            "12",
        )
        try:
            return min(max(float(raw_timeout), 2.0), 60.0)
        except (TypeError, ValueError):
            return 12.0

    @staticmethod
    def _resolve_local_cache_dir(configured_cache_dir: str | None) -> str:
        # 这个函数的作用是为内置模型选择稳定的用户级缓存目录，使首次下载完成后可以离线复用。
        configured = configured_cache_dir or os.getenv("MEMO_ECHO_MODEL_CACHE_DIR")
        default_path = Path.home() / ".memo-echo" / "models" / "fastembed"
        return str(Path(configured).expanduser() if configured else default_path)

    @staticmethod
    def _resolve_local_threads(configured_threads: int | None) -> int:
        # 这个函数的作用是限制本地 ONNX 推理线程数，避免轻量语义门控占满桌面端全部 CPU。
        raw_threads: int | str = configured_threads if configured_threads is not None else os.getenv(
            "MEMO_ECHO_EMBEDDING_THREADS",
            str(min(os.cpu_count() or 1, 4)),
        )
        try:
            return min(max(int(raw_threads), 1), 16)
        except (TypeError, ValueError):
            return min(os.cpu_count() or 1, 4)

    def uses_remote_backend(self) -> bool:
        # 这个函数的作用是仅在远程地址、密钥和模型 ID 三项都存在时启用远程覆盖，避免半配置状态造成故障。
        return bool(self.base_url and self.api_key.strip() and self.model.strip())

    def backend_name(self) -> str:
        # 这个函数的作用是提供不包含密钥的后端标识，供启动日志和故障诊断使用。
        if self.uses_remote_backend():
            return f"remote:{self.model.strip()}"
        return f"local:{self.local_model_name}"

    def is_enabled(self) -> bool:
        # 这个函数的作用是声明语义门控始终可用；没有远程配置时由项目依赖中的本地模型接管。
        return True

    async def embed(self, texts: list[str]) -> list[list[float]]:
        # 这个函数的作用是批量生成文本向量，并根据配置自动选择远程服务或内置本地模型。
        normalized_texts = [str(text).strip() for text in texts]
        if not normalized_texts or any(not text for text in normalized_texts):
            raise ValueError("embedding input must contain non-empty texts")
        if self.uses_remote_backend():
            return await self._embed_remotely(normalized_texts)
        return await self._embed_locally(normalized_texts)

    async def _embed_remotely(self, texts: list[str]) -> list[list[float]]:
        # 这个函数的作用是调用用户显式提供的 OpenAI 兼容 Embedding 服务，作为内置模型的高级覆盖项。
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": self.model,
            "input": texts,
        }
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                f"{self.base_url}/embeddings",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
            data = response.json()

        items = data.get("data") if isinstance(data, dict) else None
        if not isinstance(items, list) or len(items) != len(texts):
            raise RuntimeError("Embedding response size does not match request size")
        ordered_items = sorted(items, key=lambda item: int(item.get("index", 0)))
        raw_vectors = [
            item.get("embedding") if isinstance(item, dict) else None
            for item in ordered_items
        ]
        return self._normalize_vectors(raw_vectors, len(texts))

    async def _embed_locally(self, texts: list[str]) -> list[list[float]]:
        # 这个函数的作用是在工作线程中执行 ONNX 推理，避免模型加载和向量计算阻塞 FastAPI 事件循环。
        model = await self._get_local_model()
        raw_vectors = await asyncio.to_thread(self._run_local_embedding, model, texts)
        return self._normalize_vectors(raw_vectors, len(texts))

    async def _get_local_model(self) -> Any:
        # 这个函数的作用是并发安全地初始化一次本地模型，多个同时到达的请求会共享同一个模型实例。
        if self._local_model is not None:
            return self._local_model
        async with self._local_model_lock:
            if self._local_model is None:
                self._local_model = await asyncio.to_thread(self._create_local_model)
        return self._local_model

    def _create_local_model(self) -> Any:
        # 这个函数的作用是创建 FastEmbed 文本模型；测试可注入工厂，生产环境则使用随项目安装的实现。
        if self._local_model_factory is not None:
            return self._local_model_factory(
                self.local_model_name,
                self.local_cache_dir,
                self.local_threads,
            )

        from fastembed import TextEmbedding

        return TextEmbedding(
            model_name=self.local_model_name,
            cache_dir=self.local_cache_dir,
            threads=self.local_threads,
        )

    @staticmethod
    def _run_local_embedding(model: Any, texts: list[str]) -> list[Any]:
        # 这个函数的作用是完整消费 FastEmbed 的惰性生成器，确保线程返回前 ONNX 推理已经结束。
        batch_size = min(max(len(texts), 1), 32)
        return list(model.embed(texts, batch_size=batch_size))

    @staticmethod
    def _normalize_vectors(raw_vectors: list[Any], expected_count: int) -> list[list[float]]:
        # 这个函数的作用是把 NumPy 或普通列表统一为可序列化浮点向量，并拒绝空向量、非法值和维度漂移。
        if len(raw_vectors) != expected_count:
            raise RuntimeError("Embedding response size does not match request size")

        vectors: list[list[float]] = []
        expected_dimension: int | None = None
        for raw_vector in raw_vectors:
            values = raw_vector.tolist() if hasattr(raw_vector, "tolist") else raw_vector
            if not isinstance(values, (list, tuple)) or not values:
                raise RuntimeError("Embedding response contains an empty vector")
            vector = [float(value) for value in values]
            if any(not math.isfinite(value) for value in vector):
                raise RuntimeError("Embedding response contains a non-finite value")
            if expected_dimension is None:
                expected_dimension = len(vector)
            elif len(vector) != expected_dimension:
                raise RuntimeError("Embedding response dimensions are inconsistent")
            vectors.append(vector)
        return vectors
