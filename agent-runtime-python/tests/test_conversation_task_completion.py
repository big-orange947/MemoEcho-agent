from __future__ import annotations

import asyncio
import json

from app.schemas.events import Sender, UnifiedEvent
from app.schemas.model_profiles import ResolvedUserModelProfile
from app.schemas.profiles import ConversationProfileMatchResult
from app.services.conversation_task_completion import ConversationTaskCompletionService


class FakeEventCenterClient:
    """记录结束代理申请，避免测试依赖真实 Event Center。"""

    def __init__(self) -> None:
        self.requests: list[dict] = []

    async def request_conversation_task_completion(self, **kwargs):
        """保存服务提交的参数，并模拟持久化后的待审批状态。"""
        self.requests.append(kwargs)
        return {"status": "COMPLETION_REQUESTED"}


class FakeLlmClient:
    """按测试用例返回固定的结构化任务完成判断。"""

    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls = 0

    def is_enabled(self, model_profile=None) -> bool:
        """测试模型始终可用，确保用例能进入完成度判断。"""
        return True

    async def generate_reply(self, **kwargs) -> str:
        """返回符合完成判定服务 JSON 契约的模型结果。"""
        self.calls += 1
        return json.dumps(self.payload, ensure_ascii=False)


def build_event(text: str = "好的，明晚七点见") -> UnifiedEvent:
    """构造一条由聊天对象发来的当前私聊消息。"""
    return UnifiedEvent(
        eventId="qq:message:private:completion-001",
        platform="qq",
        scene="life",
        eventType="message",
        chatType="private",
        chatId="3807050597",
        selfId="3969785168",
        sender=Sender(id="3807050597", name="老师", role=None),
        text=text,
        timestamp="2026-07-21T12:00:00+08:00",
        rawPayload={},
    )


def build_profile_match(task_status: str = "ACTIVE") -> ConversationProfileMatchResult:
    """构造带明确目标和成功条件的已命中 Conversation Profile。"""
    return ConversationProfileMatchResult.model_validate(
        {
            "matched": True,
            "active": True,
            "profile": {
                "id": "profile-1",
                "name": "约老师打游戏",
                "platform": "qq",
                "chatType": "private",
                "chatIds": ["3807050597"],
                "supportedRoutes": ["social_reply"],
                "profileContext": {
                    "task": {
                        "objective": "约老师明晚七点一起打游戏",
                        "successCriteria": ["老师明确同意时间"],
                    }
                },
            },
            "taskState": {
                "profileId": "profile-1",
                "profileName": "约老师打游戏",
                "platform": "qq",
                "chatType": "private",
                "chatId": "3807050597",
                "status": task_status,
            },
        }
    )


def build_model_profile() -> ResolvedUserModelProfile:
    """构造可用于完成度判断的模型配置。"""
    return ResolvedUserModelProfile(
        id="model-1",
        userId="freeze",
        name="review",
        apiKey="secret",
        model="test-model",
    )


def test_requests_completion_when_peer_evidence_is_explicit() -> None:
    """对方明确确认且置信度达标时，应提交结束代理申请。"""
    event_center = FakeEventCenterClient()
    llm = FakeLlmClient(
        {
            "completed": True,
            "confidence": 0.94,
            "summary": "老师已同意明晚七点一起打游戏",
            "reason": "时间已经得到对方明确确认",
            "evidence": ["好的，明晚七点见"],
        }
    )
    service = ConversationTaskCompletionService(event_center, llm)

    result = asyncio.run(
        service.evaluate_and_request(
            event=build_event(),
            route="social_reply",
            profile_match=build_profile_match(),
            history_context=[{"role": "peer", "text": "好的，明晚七点见"}],
            final_reply="好，明晚见",
            model_profile=build_model_profile(),
        )
    )

    assert result is not None and result["requested"] is True
    assert len(event_center.requests) == 1
    assert event_center.requests[0]["evidence"] == ["好的，明晚七点见"]


def test_rejects_agent_self_evidence_even_when_model_claims_completion() -> None:
    """模型只引用 Agent 自己的话时，不得用自证内容结束代理任务。"""
    event_center = FakeEventCenterClient()
    llm = FakeLlmClient(
        {
            "completed": True,
            "confidence": 0.99,
            "summary": "任务完成",
            "reason": "Agent 已经说好",
            "evidence": ["好，明晚见"],
        }
    )
    service = ConversationTaskCompletionService(event_center, llm)

    result = asyncio.run(
        service.evaluate_and_request(
            event=build_event("我再确认一下"),
            route="social_reply",
            profile_match=build_profile_match(),
            history_context=[
                {"role": "self", "text": "好，明晚见"},
                {"role": "peer", "text": "我再确认一下"},
            ],
            final_reply="等你消息",
            model_profile=build_model_profile(),
        )
    )

    assert result == {"requested": False, "confidence": 0.99}
    assert event_center.requests == []


def test_does_not_recheck_while_completion_request_is_pending() -> None:
    """已有待审批申请时应继续代理，但不应重复调用模型或反复创建申请。"""
    event_center = FakeEventCenterClient()
    llm = FakeLlmClient({"completed": True, "confidence": 1.0, "evidence": ["好的"]})
    service = ConversationTaskCompletionService(event_center, llm)

    result = asyncio.run(
        service.evaluate_and_request(
            event=build_event(),
            route="social_reply",
            profile_match=build_profile_match("COMPLETION_REQUESTED"),
            history_context=[{"role": "peer", "text": "好的，明晚七点见"}],
            final_reply="好",
            model_profile=build_model_profile(),
        )
    )

    assert result is None
    assert llm.calls == 0
    assert event_center.requests == []
