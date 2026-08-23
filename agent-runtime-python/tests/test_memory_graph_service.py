"""MemoryGraphService 单元测试：降级开关、group_id 规范化、调用参数。

不依赖真实 Neo4j / LLM：通过注入 fake graphiti 验证行为边界。
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from app.memory.graph_service import MemoryGraphService


class FakeGraphiti:
    """记录调用并返回固定结果的假 Graphiti。"""

    def __init__(self) -> None:
        self.added: list[dict] = []
        self.searched: list[dict] = []
        self.closed = False

    async def add_episode(self, **kwargs):
        self.added.append(kwargs)
        return {"ok": True, "episode": {"uuid": "fake-episode"}}

    async def search(self, **kwargs):
        self.searched.append(kwargs)
        return [{"uuid": "edge-1", "fact": "fake fact"}]

    async def close(self):
        self.closed = True


class FakeEmbedder:
    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * 512 for _ in texts]


async def _make_service(enabled: bool = True, fake: FakeGraphiti | None = None) -> MemoryGraphService:
    service = MemoryGraphService(
        event_center_client=None,
        embedding_service=FakeEmbedder(),
        enabled=enabled,
        user_id="test-user",
    )
    if fake is not None:
        service._graphiti = fake  # 直接注入假实例，跳过懒初始化
    return service


class MemoryGraphServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_returns_safe_empty(self) -> None:
        service = await _make_service(enabled=False)
        result = await service.write_episode(
            name="n",
            episode_body="body",
            source_description="src",
            reference_time=datetime.now(timezone.utc),
        )
        self.assertIsNone(result)
        self.assertEqual(await service.search("q"), [])

    async def test_write_episode_passes_normalized_group_id(self) -> None:
        fake = FakeGraphiti()
        service = await _make_service(enabled=True, fake=fake)
        await service.write_episode(
            name="n",
            episode_body="body",
            source_description="src",
            reference_time=datetime.now(timezone.utc),
            group_id="user:qq:private:10001",
        )
        self.assertEqual(fake.added[0]["group_id"], "user_qq_private_10001")

    async def test_search_passes_normalized_group_ids(self) -> None:
        fake = FakeGraphiti()
        service = await _make_service(enabled=True, fake=fake)
        hits = await service.search("q", group_ids=["a:b", "c.d"], num_results=5)
        self.assertEqual(fake.searched[0]["group_ids"], ["a_b", "c_d"])
        self.assertEqual(fake.searched[0]["num_results"], 5)
        self.assertEqual(len(hits), 1)

    async def test_degrade_on_exception_returns_safe_empty(self) -> None:
        fake = FakeGraphiti()

        async def boom(**kwargs):
            raise RuntimeError("neo4j down")

        fake.add_episode = boom
        service = await _make_service(enabled=True, fake=fake)
        self.assertIsNone(
            await service.write_episode(
                name="n",
                episode_body="body",
                source_description="src",
                reference_time=datetime.now(timezone.utc),
            )
        )

        async def boom_search(**kwargs):
            raise RuntimeError("search down")

        fake.search = boom_search
        self.assertEqual(await service.search("q"), [])

    async def test_close_is_idempotent(self) -> None:
        fake = FakeGraphiti()
        service = await _make_service(enabled=True, fake=fake)
        await service.close()
        await service.close()
        self.assertTrue(fake.closed)

    def test_normalize_group_id(self) -> None:
        self.assertEqual(MemoryGraphService.normalize_group_id("a:b:c"), "a_b_c")
        self.assertEqual(MemoryGraphService.normalize_group_id("a.b/c"), "a_b_c")
        self.assertEqual(MemoryGraphService.normalize_group_id("ok-1_2"), "ok-1_2")
        self.assertIsNone(MemoryGraphService.normalize_group_id(None))
        self.assertEqual(MemoryGraphService.normalize_group_id(""), "")


if __name__ == "__main__":
    unittest.main()
