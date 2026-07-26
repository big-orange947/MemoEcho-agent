from __future__ import annotations

import unittest

from app.memory.candidate_extractor import MemoryCandidateExtractor
from app.schemas.events import UnifiedEvent
from app.schemas.profiles import ConversationProfile, ConversationProfileMatchResult


class FakeLlmClient:
    """返回固定结构化候选，便于验证提取器边界而不访问真实模型。"""

    def is_enabled(self, model_profile=None) -> bool:
        """测试模型始终可用。"""
        return True

    async def generate_reply(self, system_prompt: str, user_message: str, **kwargs) -> str:
        """返回一条高置信稳定事实和一条应被阈值过滤的低置信结果。"""
        return (
            '{"candidates":['
            '{"predicate":"所在城市","value":"大连","confidence":0.94,"expiresAt":null},'
            '{"predicate":"临时状态","value":"有点困","confidence":0.3,"expiresAt":null}'
            ']}'
        )


class FakeEventCenterClient:
    """记录写回候选，验证提取器不会直接确认事实。"""

    def __init__(self) -> None:
        self.created = []

    async def create_memory_candidate(self, event, candidate) -> dict:
        """保存调用参数并模拟 Event Center 的候选响应。"""
        self.created.append((event, candidate))
        return {"id": "candidate-1", "status": "CANDIDATE"}


class MemoryCandidateExtractorTest(unittest.IsolatedAsyncioTestCase):
    """验证长期记忆提取授权、身份约束和置信度过滤。"""

    async def test_extracts_only_high_confidence_owner_fact_when_authorized(self) -> None:
        """授权会话中的 OWNER 消息应只写入高置信候选。"""
        event_center = FakeEventCenterClient()
        extractor = MemoryCandidateExtractor(event_center, FakeLlmClient())

        stored = await extractor.extract_and_store(self._event("OWNER"), None)

        self.assertEqual(1, len(stored))
        self.assertEqual("所在城市", event_center.created[0][1].predicate)
        self.assertEqual("CANDIDATE", stored[0]["status"])

    def test_rejects_agent_message_even_when_profile_is_authorized(self) -> None:
        """Agent 代发消息不能进入个人长期记忆提取流程。"""
        extractor = MemoryCandidateExtractor(FakeEventCenterClient(), FakeLlmClient())

        eligible = extractor._is_eligible(self._event("AGENT"), self._profile_match(True), None)

        self.assertFalse(eligible)

    def test_rejects_owner_message_without_independent_consent(self) -> None:
        """读取历史或训练授权不能替代长期记忆候选授权。"""
        extractor = MemoryCandidateExtractor(FakeEventCenterClient(), FakeLlmClient())

        eligible = extractor._is_eligible(self._event("OWNER"), self._profile_match(False), None)

        self.assertFalse(eligible)

    @staticmethod
    def _event(actor_type: str) -> UnifiedEvent:
        """构造一条最小 QQ 私聊事件。"""
        return UnifiedEvent.model_validate({
            "eventId": "event-1",
            "platform": "qq",
            "scene": "life",
            "eventType": "message",
            "chatType": "private",
            "chatId": "friend-1",
            "selfId": "owner-1",
            "sender": {"id": "owner-1", "name": "freeze"},
            "text": "我长期住在大连",
            "timestamp": "2026-07-17T10:00:00+08:00",
            "actorType": actor_type,
        })

    @staticmethod
    def _profile_match(enabled: bool) -> ConversationProfileMatchResult:
        """构造带独立记忆授权的会话设定。"""
        profile = ConversationProfile.model_validate({
            "id": "profile-1",
            "name": "朋友私聊",
            "profileContext": {"memoryPolicy": {"extractionEnabled": enabled}},
        })
        return ConversationProfileMatchResult(matched=True, active=True, profile=profile)
