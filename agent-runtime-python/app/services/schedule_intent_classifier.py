from __future__ import annotations

import asyncio
import math
import os

from app.agents.schedule_extractor import ScheduleExtractor
from app.clients.embedding_service import EmbeddingServiceClient
from app.schemas.schedules import SemanticIntentDecision


class SemanticScheduleIntentClassifier:
    """使用少量意图原型完成日程语义门控，不参与字段抽取和事实生成。"""

    PROTOTYPES: dict[str, tuple[str, ...]] = {
        "schedule_create": (
            "明天下午三点开项目例会",
            "周五之前提醒我提交课程报告",
            "下周二上午十点在会议室讨论方案",
            "帮我记一下月底要交房租",
            "今晚八点和张老师线上沟通",
            "三天后提醒我给客户回电话",
        ),
        "schedule_query": (
            "我今天有什么日程",
            "帮我看看最近的安排",
            "下周有哪些会议",
            "刚才记录了什么提醒",
            "明天有空吗",
            "列出我的日程",
        ),
        "non_schedule": (
            "今天天气怎么样",
            "这个游戏角色怎么培养",
            "帮我总结刚才的聊天",
            "你好最近怎么样",
            "把这个文件整理成表格",
            "这张图片里是什么",
        ),
    }

    def __init__(
        self,
        client: EmbeddingServiceClient,
        minimum_score: float | None = None,
        minimum_margin: float | None = None,
        evidence_minimum_score: float | None = None,
        evidence_minimum_margin: float | None = None,
        extractor: ScheduleExtractor | None = None,
    ) -> None:
        # 这个构造函数的作用是保存向量客户端、规则提取器和两组判定阈值，并保证原型只构建一次。
        self.client = client
        self.extractor = extractor or ScheduleExtractor()
        self.minimum_score = self._read_threshold(
            minimum_score,
            "SCHEDULE_INTENT_MIN_SCORE",
            0.68,
        )
        self.minimum_margin = self._read_threshold(
            minimum_margin,
            "SCHEDULE_INTENT_MIN_MARGIN",
            0.06,
        )
        # 明确日期和时间已经提供了强结构证据，此时只需要向量模型确认消息语义更接近“创建日程”。
        # 该阈值仅用于证据充分的消息，不会放宽普通聊天的全局判定标准。
        self.evidence_minimum_score = self._read_threshold(
            evidence_minimum_score,
            "SCHEDULE_INTENT_EVIDENCE_MIN_SCORE",
            0.64,
        )
        self.evidence_minimum_margin = self._read_threshold(
            evidence_minimum_margin,
            "SCHEDULE_INTENT_EVIDENCE_MIN_MARGIN",
            0.04,
        )
        self._centroids: dict[str, list[float]] | None = None
        self._centroid_lock = asyncio.Lock()

    @classmethod
    def build_default(cls) -> "SemanticScheduleIntentClassifier":
        # 这个函数的作用是构建默认分类器；没有远程配置时客户端会自动启用内置中文向量模型。
        return cls(EmbeddingServiceClient())

    @staticmethod
    def _read_threshold(configured: float | None, env_name: str, default: float) -> float:
        # 这个函数的作用是安全读取相似度阈值，并把异常配置回退到保守默认值。
        raw_value = configured if configured is not None else os.getenv(env_name, str(default))
        try:
            return min(max(float(raw_value), 0.0), 1.0)
        except (TypeError, ValueError):
            return default

    def is_enabled(self) -> bool:
        # 这个函数的作用是向编排层公开语义门控可用状态；默认本地后端使该状态不再依赖用户配置。
        return self.client.is_enabled()

    async def warm_up(self) -> None:
        """提前生成意图原型中心，避免第一条实时消息承担模型初始化成本。"""
        if self.is_enabled():
            await self._ensure_centroids()

    async def classify(self, text: str) -> SemanticIntentDecision:
        # 这个函数的作用是计算消息与各类意图原型的相似度，仅在分数和区分度同时足够时给出决定。
        normalized_text = " ".join(str(text or "").split())
        if not normalized_text or not self.is_enabled():
            return SemanticIntentDecision()

        await self._ensure_centroids()
        query_vectors = await self.client.embed([normalized_text])
        query_vector = query_vectors[0]
        scores = {
            label: self._cosine_similarity(query_vector, centroid)
            for label, centroid in (self._centroids or {}).items()
        }
        if not scores:
            return SemanticIntentDecision()

        ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        top_label, top_score = ranked[0]
        runner_up_score = ranked[1][1] if len(ranked) > 1 else 0.0
        margin = top_score - runner_up_score
        standard_decision = top_score >= self.minimum_score and margin >= self.minimum_margin
        evidence_backed_decision = (
            top_label == "schedule_create"
            and self._has_explicit_datetime_evidence(normalized_text)
            and top_score >= self.evidence_minimum_score
            and margin >= self.evidence_minimum_margin
        )
        decisive = standard_decision or evidence_backed_decision
        route = "schedule_extract" if decisive and top_label == "schedule_create" else None
        return SemanticIntentDecision(
            label=top_label,
            route=route,
            score=round(top_score, 6),
            margin=round(margin, 6),
            decisive=decisive,
        )

    def _has_explicit_datetime_evidence(self, text: str) -> bool:
        # 这个函数的作用是确认原文同时给出了唯一日期和唯一时刻，避免仅凭相似度把闲聊改路由。
        candidate = self.extractor.extract(text)
        return bool(
            candidate.start_time
            and candidate.date_is_explicit
            and candidate.time_is_explicit
            and not candidate.ambiguous
        )

    async def _ensure_centroids(self) -> None:
        # 这个函数的作用是并发安全地生成一次原型向量，并把同一意图的多个示例合成为中心向量。
        if self._centroids is not None:
            return
        async with self._centroid_lock:
            if self._centroids is not None:
                return

            labels: list[str] = []
            prototype_texts: list[str] = []
            for label, examples in self.PROTOTYPES.items():
                for example in examples:
                    labels.append(label)
                    prototype_texts.append(example)

            vectors = await self.client.embed(prototype_texts)
            grouped_vectors: dict[str, list[list[float]]] = {}
            for label, vector in zip(labels, vectors, strict=True):
                grouped_vectors.setdefault(label, []).append(vector)
            self._centroids = {
                label: self._average_vectors(group)
                for label, group in grouped_vectors.items()
            }

    @staticmethod
    def _average_vectors(vectors: list[list[float]]) -> list[float]:
        # 这个函数的作用是计算同类原型的中心向量，并拒绝维度不一致的供应商返回结果。
        if not vectors:
            raise ValueError("prototype vectors must not be empty")
        dimension = len(vectors[0])
        if dimension == 0 or any(len(vector) != dimension for vector in vectors):
            raise ValueError("prototype vector dimensions are inconsistent")
        return [
            sum(vector[index] for vector in vectors) / len(vectors)
            for index in range(dimension)
        ]

    @staticmethod
    def _cosine_similarity(left: list[float], right: list[float]) -> float:
        # 这个函数的作用是计算两个向量的余弦相似度，空向量或维度错误时返回最低可信分数。
        if not left or len(left) != len(right):
            return -1.0
        left_norm = math.sqrt(sum(value * value for value in left))
        right_norm = math.sqrt(sum(value * value for value in right))
        if left_norm == 0.0 or right_norm == 0.0:
            return -1.0
        dot_product = sum(left_value * right_value for left_value, right_value in zip(left, right, strict=True))
        return dot_product / (left_norm * right_norm)
