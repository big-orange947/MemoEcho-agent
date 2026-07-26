from __future__ import annotations

import asyncio
import os
import unittest
from unittest.mock import patch

from app.clients.embedding_service import EmbeddingServiceClient


class FakeLocalEmbeddingModel:
    """模拟 FastEmbed 的惰性向量生成接口，测试时不下载真实模型。"""

    def __init__(self) -> None:
        # 这个构造函数的作用是记录本地模型实际执行了多少批推理。
        self.call_count = 0

    def embed(self, texts: list[str], batch_size: int):
        # 这个函数的作用是为每条文本返回固定维度向量，并保持与 FastEmbed 相同的生成器语义。
        self.call_count += 1
        for text in texts:
            yield [float(len(text)), float(batch_size), 1.0]


class EmbeddingServiceClientTest(unittest.IsolatedAsyncioTestCase):
    async def test_should_use_builtin_local_backend_without_configuration(self) -> None:
        # 这个测试函数的作用是验证清空全部远程环境变量后，客户端仍会使用本地模型正常生成向量。
        created_models: list[FakeLocalEmbeddingModel] = []

        def create_model(model_name: str, cache_dir: str, threads: int) -> FakeLocalEmbeddingModel:
            # 这个测试工厂函数的作用是校验默认本地参数，并返回无需下载的假模型。
            self.assertEqual(model_name, EmbeddingServiceClient.DEFAULT_LOCAL_MODEL)
            self.assertTrue(cache_dir.endswith("fastembed"))
            self.assertGreaterEqual(threads, 1)
            model = FakeLocalEmbeddingModel()
            created_models.append(model)
            return model

        with patch.dict(
            os.environ,
            {
                "EMBEDDING_BASE_URL": "",
                "EMBEDDING_API_KEY": "",
                "EMBEDDING_MODEL": "",
            },
            clear=False,
        ):
            client = EmbeddingServiceClient(local_model_factory=create_model)
            vectors = await client.embed(["明天下午开会", "普通聊天"])

        self.assertTrue(client.is_enabled())
        self.assertEqual(client.backend_name(), "local:BAAI/bge-small-zh-v1.5")
        self.assertEqual(vectors, [[6.0, 2.0, 1.0], [4.0, 2.0, 1.0]])
        self.assertEqual(len(created_models), 1)

    async def test_should_initialize_local_model_only_once_for_concurrent_requests(self) -> None:
        # 这个测试函数的作用是验证后台预热和实时请求并发发生时只创建一个 ONNX 模型实例。
        factory_call_count = 0

        def create_model(_model_name: str, _cache_dir: str, _threads: int) -> FakeLocalEmbeddingModel:
            # 这个测试工厂函数的作用是统计本地模型初始化次数。
            nonlocal factory_call_count
            factory_call_count += 1
            return FakeLocalEmbeddingModel()

        client = EmbeddingServiceClient(
            base_url="",
            api_key="",
            model="",
            local_model_factory=create_model,
        )
        await asyncio.gather(
            client.embed(["第一条"]),
            client.embed(["第二条"]),
        )

        self.assertEqual(factory_call_count, 1)


if __name__ == "__main__":
    unittest.main()
