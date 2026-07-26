from __future__ import annotations

import asyncio

from app.schemas.conversation_cognition import ConversationCognitionRequest
from app.schemas.model_profiles import ResolvedUserModelProfile, UserModelProfileResolveResult
from app.services.conversation_cognition_service import ConversationCognitionService


class FakeEventCenterClient:
    """提供固定模型配置，避免单元测试访问真实 Event Center。"""

    async def resolve_user_model_profile(self, route: str, user_id: str):
        # 这个函数的作用是验证认知分析统一复用聊天摘要模型路由。
        assert route == "chat_summary"
        assert user_id == "user-1"
        return UserModelProfileResolveResult(
            matched=True,
            profile=ResolvedUserModelProfile(
                id="model-1",
                userId="user-1",
                name="summary",
                apiKey="secret",
                model="test-model",
            ),
        )


class FakeLlmClient:
    """记录模型输入并返回可控 JSON，便于验证身份标签和结构解析。"""

    def __init__(self, reply: str) -> None:
        # 这个构造函数的作用是保存预期模型结果和最近一次时间线。
        self.reply = reply
        self.last_user_message = ""

    def is_enabled(self, model_profile=None):
        # 这个函数的作用是让测试模型始终处于已配置状态。
        return True

    async def generate_reply(self, system_prompt: str, user_message: str, **kwargs):
        # 这个函数的作用是捕获时间线并返回固定结构化结果。
        self.last_user_message = user_message
        return self.reply


def build_request(messages: list[dict]) -> ConversationCognitionRequest:
    """创建带完整会话作用域的认知分析请求。"""
    return ConversationCognitionRequest.model_validate({
        "userId": "user-1",
        "platform": "qq",
        "chatType": "private",
        "chatId": "10001",
        "messages": messages,
    })


def test_analyze_returns_structured_card_and_keeps_speaker_roles():
    """验证认知分析会区分人工发送、Agent 代发和对方消息，并解析字段置信度。"""
    llm = FakeLlmClient("""
        ```json
        {
          "relationship": {"value": "同学", "confidence": 0.82},
          "preferredAddress": {"value": "小林", "confidence": 0.74},
          "counterpartyTraits": {"value": "偏好直接确认时间", "confidence": 0.61},
          "ownerExpressionHabits": {"value": "人工消息较简短", "confidence": 0.66},
          "counterpartyExpressionHabits": {"value": "常用短问句", "confidence": 0.7},
          "backgroundSummary": {"value": "双方正在约见面时间", "confidence": 0.9},
          "currentProgress": {"value": "对方等待确认地点", "confidence": 0.95},
          "knownFacts": ["双方计划明天下午见面"],
          "recentTopics": ["见面时间", "地点"],
          "openQuestions": ["最终地点在哪里"]
        }
        ```
    """)
    service = ConversationCognitionService(FakeEventCenterClient(), llm)

    result = asyncio.run(service.analyze(build_request([
        {"eventId": "1", "text": "明天下午有空吗", "timestamp": "2026-07-20T10:00:00Z", "messageOrigin": "EXTERNAL"},
        {"eventId": "2", "senderRole": "self", "text": "有空", "timestamp": "2026-07-20T10:01:00Z", "messageOrigin": "USER_MANUAL"},
        {"eventId": "3", "senderRole": "self", "text": "那就三点", "timestamp": "2026-07-20T10:02:00Z", "messageOrigin": "AGENT_AUTO"},
    ])))

    assert result.generated_by_model is True
    assert result.relationship.value == "同学"
    assert result.relationship.confidence == 0.82
    assert result.source_event_ids == ["1", "2", "3"]
    assert "[对方]" in llm.last_user_message
    assert "[我]" in llm.last_user_message
    assert "[Agent代发]" in llm.last_user_message


def test_analyze_falls_back_without_inventing_personality():
    """验证模型异常时只保存客观轮次状态，不生成关系、性格或称呼。"""
    class BrokenLlmClient(FakeLlmClient):
        async def generate_reply(self, system_prompt: str, user_message: str, **kwargs):
            # 这个函数的作用是模拟供应商超时。
            raise RuntimeError("timeout")

    service = ConversationCognitionService(FakeEventCenterClient(), BrokenLlmClient(""))
    result = asyncio.run(service.analyze(build_request([
        {"eventId": "1", "text": "地点定了吗", "timestamp": "2026-07-20T10:00:00Z", "messageOrigin": "EXTERNAL"},
    ])))

    assert result.generated_by_model is False
    assert result.relationship.value == ""
    assert result.counterparty_traits.value == ""
    assert result.current_progress.confidence == 1.0
    assert "轮到账号主人" in result.current_progress.value
