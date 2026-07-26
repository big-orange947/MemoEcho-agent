from __future__ import annotations

import asyncio

from app.schemas.conversation_progress import ConversationProgressRequest
from app.schemas.model_profiles import ResolvedUserModelProfile, UserModelProfileResolveResult
from app.services.conversation_progress_service import ConversationProgressService


class FakeEventCenterClient:
    async def resolve_user_model_profile(self, route: str, user_id: str):
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
    def __init__(self, reply: str = "对方正在确认会员购买方式，我方已说明月付价格，目前轮到对方继续确认"):
        self.reply = reply
        self.last_user_message = ""

    def is_enabled(self, model_profile=None):
        return True

    async def generate_reply(self, system_prompt: str, user_message: str, **kwargs):
        self.last_user_message = user_message
        return self.reply


def build_request(messages: list[dict]) -> ConversationProgressRequest:
    return ConversationProgressRequest.model_validate({
        "userId": "user-1",
        "platform": "qq",
        "chatType": "private",
        "chatId": "10001",
        "messages": messages,
    })


def test_summarize_uses_model_and_keeps_speaker_labels():
    """验证按需摘要会明确区分对方与 Agent 代发，避免模型颠倒聊天双方。"""
    llm = FakeLlmClient()
    service = ConversationProgressService(FakeEventCenterClient(), llm)

    result = asyncio.run(service.summarize(build_request([
        {"eventId": "1", "senderName": "小号", "text": "一个月多少钱", "timestamp": "2026-07-13T10:00:00Z", "messageOrigin": "EXTERNAL"},
        {"eventId": "2", "senderName": "我", "senderRole": "self", "text": "一个月15", "timestamp": "2026-07-13T10:01:00Z", "messageOrigin": "AGENT_AUTO"},
    ])))

    assert result.generated_by_model is True
    assert "对方" in llm.last_user_message
    assert "Agent代发" in llm.last_user_message
    assert result.summary.startswith("对方正在确认")


def test_summarize_falls_back_when_model_fails():
    """验证模型失败时仍返回基于最后双方消息的自然语言进度，不影响上下文查看。"""
    class BrokenLlmClient(FakeLlmClient):
        async def generate_reply(self, system_prompt: str, user_message: str, **kwargs):
            raise RuntimeError("timeout")

    service = ConversationProgressService(FakeEventCenterClient(), BrokenLlmClient())
    result = asyncio.run(service.summarize(build_request([
        {"eventId": "1", "senderName": "小号", "text": "会员还卖吗", "timestamp": "2026-07-13T10:00:00Z", "messageOrigin": "EXTERNAL"},
    ])))

    assert result.generated_by_model is False
    assert "会员还卖吗" in result.summary
    assert "等待我方回应" in result.summary
