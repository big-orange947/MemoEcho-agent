from app.orchestrator.service import OrchestratorService
from app.schemas.results import AgentResult


def test_urgent_dispatch_should_build_immediate_high_priority_notification() -> None:
    """验证私聊或 @ 消息会被映射为立即展示的高优先级通知。"""
    result = AgentResult(
        task_id="task-1",
        agent="inbox_dispatch",
        status="success",
        structured_result={
            "dispatchMode": "urgent",
            "urgencyReason": "at_self",
            "shouldNotifyNow": True,
            "aggregationKey": "qq:group:1001",
            "bufferedCount": 0,
            "flushed": False,
        },
    )

    decision = OrchestratorService._build_notification_decision([result])

    assert decision is not None
    assert decision.channel == "urgent"
    assert decision.priority == "HIGH"
    assert decision.trigger_reason == "at_self"
    assert decision.notify_now is True
    assert decision.aggregation_status == "IMMEDIATE"


def test_normal_dispatch_should_distinguish_buffering_and_ready_summary() -> None:
    """验证慢通道未达到阈值时缓冲，到达阈值后才变为可展示的摘要。"""
    buffered_result = AgentResult(
        task_id="task-2",
        agent="inbox_dispatch",
        status="success",
        structured_result={
            "dispatchMode": "normal",
            "urgencyReason": "none",
            "shouldNotifyNow": False,
            "aggregationKey": "qq:group:1001",
            "bufferedCount": 3,
            "flushed": False,
            "summaryCandidate": None,
        },
    )
    ready_result = buffered_result.model_copy(
        update={
            "structured_result": {
                **buffered_result.structured_result,
                "shouldNotifyNow": True,
                "bufferedCount": 10,
                "flushed": True,
                "summaryCandidate": "过去一段时间群里主要提到：项目进度和周报截止时间。",
            }
        }
    )

    buffered = OrchestratorService._build_notification_decision([buffered_result])
    ready = OrchestratorService._build_notification_decision([ready_result])

    assert buffered is not None
    assert buffered.priority == "LOW"
    assert buffered.aggregation_status == "BUFFERED"
    assert buffered.buffered_count == 3
    assert ready is not None
    assert ready.priority == "NORMAL"
    assert ready.aggregation_status == "SUMMARY_READY"
    assert ready.notify_now is True
    assert ready.summary_candidate.startswith("过去一段时间")


def test_policy_suppressed_message_should_not_be_reported_as_buffered() -> None:
    """验证静默或仅重点策略抑制的消息会向工作台明确暴露为 SUPPRESSED。"""
    result = AgentResult(
        task_id="task-3",
        agent="inbox_dispatch",
        status="success",
        structured_result={
            "dispatchMode": "normal",
            "urgencyReason": "muted",
            "shouldNotifyNow": False,
            "aggregationKey": "qq:group:1001",
            "bufferedCount": 0,
            "flushed": False,
            "suppressedByPolicy": True,
        },
    )

    decision = OrchestratorService._build_notification_decision([result])

    assert decision is not None
    assert decision.priority == "NONE"
    assert decision.aggregation_status == "SUPPRESSED"
