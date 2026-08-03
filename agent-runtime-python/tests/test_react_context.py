from __future__ import annotations

from datetime import datetime, timezone

from app.services.react_context import CandidateReplyGuard, MasterTaskContextBuilder


def test_should_build_model_context_without_control_metadata() -> None:
    """模型工作上下文只能保留任务事实和聊天证据，不能携带定位或持久化字段。"""
    context = MasterTaskContextBuilder().build(
        task={"id": "task-1", "userId": "user-1", "chatId": "chat-1", "targetName": "内部代号", "targetQuery": "内部代号", "objective": "确认明晚课程时间", "successCriteria": "对方确认时间", "deadlineText": "明晚"},
        timeline=[{"eventId": "e-1", "at": "2026-07-23T10:00:00+08:00", "speaker": "对方", "text": "晚上可以"}],
        pre_task_history=[],
        previous_state={"workingMemory": {"progress": "等待确认", "chatId": "chat-1"}},
        task_created_at="2026-07-23T09:00:00+08:00",
        current_time=datetime(2026, 7, 23, 10, 1, tzinfo=timezone.utc),
        resolved_time_text="2026-07-23 晚上七点至九点",
        history_access_allowed=True,
        available_tools=["send_qq_message"],
    )

    payload = context.to_model_payload()
    assert payload["taskGoal"] == "确认明晚课程时间"
    # 时间线必须保留双方角色、方向、来源和事件 ID，供 Agent 区分谁在何时说了什么。
    assert payload["conversationTimeline"] == [
        {
            "at": "2026-07-23T10:00:00+08:00",
            "role": "对方",
            "speaker": "对方",
            "direction": "",
            "actorType": "",
            "messageOrigin": "",
            "eventId": "e-1",
            "platformMessageId": "",
            "clientMessageId": "",
            "text": "晚上可以",
        }
    ]
    assert "taskId" not in payload
    assert "targetName" not in payload
    assert "chatId" not in payload["workingMemory"]


def test_should_block_dynamic_internal_term_without_static_ban_list() -> None:
    """控制术语由任务数据动态得出，避免依赖特定联系人名称的硬编码规则。"""
    builder = MasterTaskContextBuilder()
    terms = builder.internal_terms({"targetName": "内部代号", "targetQuery": "内部代号"})
    assert not CandidateReplyGuard().validate("内部代号，晚上见", terms).allowed
    assert CandidateReplyGuard().validate("晚上见", terms).allowed
