from __future__ import annotations

import unittest

from app.services.schedule_intent_classifier import SemanticScheduleIntentClassifier


class FakeEmbeddingClient:
    """用正交向量模拟三类语义，验证门控算法而不依赖外部 Embedding 服务。"""

    def is_enabled(self) -> bool:
        # 这个函数的作用是模拟已启用的向量配置。
        return True

    async def embed(self, texts: list[str]) -> list[list[float]]:
        # 这个函数的作用是按测试文本内容返回可预测的日程、查询或普通消息向量。
        vectors: list[list[float]] = []
        for text in texts:
            if text in SemanticScheduleIntentClassifier.PROTOTYPES["schedule_create"] or "两点开会" in text:
                vectors.append([1.0, 0.0, 0.0])
            elif text in SemanticScheduleIntentClassifier.PROTOTYPES["schedule_query"] or "什么日程" in text:
                vectors.append([0.0, 1.0, 0.0])
            else:
                vectors.append([0.0, 0.0, 1.0])
        return vectors


class SemanticScheduleIntentClassifierTest(unittest.IsolatedAsyncioTestCase):
    async def test_should_route_only_high_confidence_create_intent(self) -> None:
        # 这个测试函数的作用是验证创建意图可以补充路由，而查询意图不会误触发新增日程链路。
        classifier = SemanticScheduleIntentClassifier(
            FakeEmbeddingClient(),
            minimum_score=0.8,
            minimum_margin=0.2,
        )

        create_decision = await classifier.classify("后天下午两点开会")
        query_decision = await classifier.classify("我今天有什么日程")

        self.assertTrue(create_decision.decisive)
        self.assertEqual(create_decision.route, "schedule_extract")
        self.assertTrue(query_decision.decisive)
        self.assertIsNone(query_decision.route)

    async def test_should_use_relaxed_threshold_only_with_explicit_datetime_evidence(self) -> None:
        # 这个测试函数的作用是验证明确日期时间可以辅助临界语义分数，但不能让无时间的普通消息误入日程链路。
        class BorderlineEmbeddingClient(FakeEmbeddingClient):
            async def embed(self, texts: list[str]) -> list[list[float]]:
                vectors = await super().embed(texts)
                if len(texts) == 1:
                    return [[0.67, 0.50, 0.548726]]
                return vectors

        classifier = SemanticScheduleIntentClassifier(
            BorderlineEmbeddingClient(),
            minimum_score=0.8,
            minimum_margin=0.2,
            evidence_minimum_score=0.64,
            evidence_minimum_margin=0.04,
        )

        explicit = await classifier.classify("后天上午九点到十一点在实验室进行设备测试")
        ordinary = await classifier.classify("有空的时候测试一下设备")

        self.assertTrue(explicit.decisive)
        self.assertEqual(explicit.route, "schedule_extract")
        self.assertFalse(ordinary.decisive)
        self.assertIsNone(ordinary.route)


if __name__ == "__main__":
    unittest.main()
