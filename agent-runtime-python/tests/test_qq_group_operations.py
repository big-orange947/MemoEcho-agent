from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.agents.groupops_agent import GroupOpsAgent
from app.schemas.events import Sender, UnifiedEvent
from app.schemas.tasks import AgentTaskContext
from app.tools.qq_group_operations_tool import (
    GroupOperationApprovalRegistry,
    ManageQqGroupTool,
    QueryQqGroupTool,
)
from app.tools.registry import ToolRegistry
from tool_test_utils import register_test_tool


class FakeConnectorClient:
    """记录测试期间的 Connector 调用，避免真实操作 QQ 群。"""

    def __init__(self) -> None:
        self.queries: list[tuple[str, int]] = []
        self.operations: list[dict] = []

    async def query_group(self, action: str, group_id: int) -> dict:
        self.queries.append((action, group_id))
        return {"status": "ok", "data": [{"user_id": 1}, {"user_id": 2}]}

    async def execute_group_operation(self, operation: dict) -> dict:
        self.operations.append(operation)
        return {"status": "ok", "retcode": 0, "data": None}


def build_context(
    text: str,
    allowed_tools: list[str],
    mentions: list[str] | None = None,
    sender_id: str = "3969785168",
) -> AgentTaskContext:
    """构造群消息上下文，供解析、权限和审批测试复用。"""
    event = UnifiedEvent(
        eventId="qq:group:test-message",
        platform="qq",
        eventType="message",
        chatType="group",
        chatId="1098307542",
        selfId="3969785168",
        sender=Sender(id=sender_id, name="freeze", role="owner"),
        text=text,
        mentions=mentions or [],
        timestamp="2026-07-16T12:00:00+08:00",
    )
    return AgentTaskContext(
        task_id="task-1",
        route="group_ops",
        event=event,
        allowed_tools=allowed_tools,
    )


def test_read_only_tool_only_accepts_allowlisted_queries() -> None:
    client = FakeConnectorClient()
    tool = QueryQqGroupTool(client)

    result = asyncio.run(tool.query(action="member_list", group_id=1098307542))

    assert result["status"] == "ok"
    assert client.queries == [("member_list", 1098307542)]
    with pytest.raises(ValueError):
        asyncio.run(tool.query(action="kick_member", group_id=1098307542))


def test_write_operation_requires_one_time_confirmation(tmp_path: Path) -> None:
    client = FakeConnectorClient()
    approvals = GroupOperationApprovalRegistry(audit_path=str(tmp_path / "audit.jsonl"))
    tool = ManageQqGroupTool(client, approvals)

    proposal = asyncio.run(
        tool.prepare(
            action="mute_member",
            group_id=1098307542,
            target_user_id=3807050597,
            duration_seconds=600,
            event_id="event-1",
            requester_id="2597164807",
        )
    )
    assert client.operations == []
    assert proposal["status"] == "confirmation_required"

    assert "approvalToken" not in proposal
    result = asyncio.run(tool.approve_event("event-1", "确认执行"))

    assert result["status"] == "success"
    assert client.operations[0]["action"] == "mute_member"
    with pytest.raises(ValueError):
        asyncio.run(tool.approve_event("event-1", "确认执行"))


def test_high_risk_operation_rejects_short_confirmation(tmp_path: Path) -> None:
    client = FakeConnectorClient()
    tool = ManageQqGroupTool(
        client,
        GroupOperationApprovalRegistry(audit_path=str(tmp_path / "audit.jsonl")),
    )
    proposal = asyncio.run(
        tool.prepare(
            action="kick_member",
            group_id=1098307542,
            target_user_id=3807050597,
            event_id="event-2",
            requester_id="2597164807",
        )
    )

    with pytest.raises(ValueError, match="确认短语不匹配"):
        asyncio.run(tool.approve_event("event-2", "确认执行"))
    assert client.operations == []


def test_invalid_operation_is_rejected_before_approval(tmp_path: Path) -> None:
    client = FakeConnectorClient()
    tool = ManageQqGroupTool(
        client,
        GroupOperationApprovalRegistry(audit_path=str(tmp_path / "audit.jsonl")),
    )

    with pytest.raises(ValueError, match="target_user_id"):
        asyncio.run(
            tool.prepare(
                action="kick_member",
                group_id=1098307542,
                event_id="event-1",
                requester_id="2597164807",
            )
        )


def test_audit_log_does_not_store_raw_approval_token(tmp_path: Path) -> None:
    client = FakeConnectorClient()
    audit_path = tmp_path / "audit.jsonl"
    tool = ManageQqGroupTool(
        client,
        GroupOperationApprovalRegistry(audit_path=str(audit_path)),
    )

    proposal = asyncio.run(
        tool.prepare(
            action="set_group_name",
            group_id=1098307542,
            text="新群名",
            event_id="event-1",
            requester_id="2597164807",
        )
    )

    internal_proposal = tool.approvals.find_by_event_id("event-1")
    assert internal_proposal is not None
    audit_text = audit_path.read_text(encoding="utf-8")
    assert "approvalToken" not in proposal
    assert internal_proposal.token not in audit_text
    assert "approvalId" in audit_text


def test_event_approval_keeps_token_inside_runtime(tmp_path: Path) -> None:
    client = FakeConnectorClient()
    tool = ManageQqGroupTool(
        client,
        GroupOperationApprovalRegistry(audit_path=str(tmp_path / "audit.jsonl")),
    )
    asyncio.run(
        tool.prepare(
            action="publish_notice",
            group_id=1098307542,
            text="周五提交周报",
            event_id="event-notice-1",
            requester_id="2597164807",
        )
    )

    pending = tool.pending_for_event("event-notice-1")

    assert pending is not None
    assert "approvalToken" not in pending
    assert pending["confirmationPhrase"] == "确认执行 publish_notice 1098307542"
    result = asyncio.run(tool.approve_event("event-notice-1", pending["confirmationPhrase"]))
    assert result["status"] == "success"
    assert tool.pending_for_event("event-notice-1") is None


def test_agent_blocks_write_when_privileged_tool_is_not_enabled(tmp_path: Path) -> None:
    client = FakeConnectorClient()
    registry = ToolRegistry()
    register_test_tool(registry, "query_qq_group", QueryQqGroupTool(client))
    register_test_tool(
        registry,
        "manage_qq_group",
        ManageQqGroupTool(
            client,
            GroupOperationApprovalRegistry(audit_path=str(tmp_path / "audit.jsonl")),
        ),
    )
    agent = GroupOpsAgent(registry)
    context = build_context("禁言 3807050597 10分钟", ["query_qq_group"])

    result = asyncio.run(agent.run(context, "handle_group_ops"))

    assert result.status == "blocked"
    assert result.need_confirmation is False
    assert client.operations == []


def test_agent_creates_approval_for_explicitly_enabled_write_tool(tmp_path: Path) -> None:
    client = FakeConnectorClient()
    registry = ToolRegistry()
    register_test_tool(registry, "query_qq_group", QueryQqGroupTool(client))
    register_test_tool(
        registry,
        "manage_qq_group",
        ManageQqGroupTool(
            client,
            GroupOperationApprovalRegistry(audit_path=str(tmp_path / "audit.jsonl")),
        ),
    )
    agent = GroupOpsAgent(registry)
    context = build_context(
        "禁言十分钟",
        ["query_qq_group", "manage_qq_group"],
        mentions=["3969785168", "3807050597"],
    )
    # 中文数字未被确定性解析，参数不完整时必须询问，不能猜测时长。
    unresolved = asyncio.run(agent.run(context, "handle_group_ops"))
    assert unresolved.status == "needs_clarification"

    context.event.text = "禁言10分钟"
    proposal = asyncio.run(agent.run(context, "handle_group_ops"))

    assert proposal.status == "confirmation_required"
    assert proposal.need_confirmation is True
    assert proposal.structured_result["approval"]["risk"] == "MEDIUM"
    assert "approvalToken" not in proposal.structured_result["approval"]
    assert client.operations == []


def test_group_member_cannot_trigger_privileged_operation(tmp_path: Path) -> None:
    """验证普通群成员即使说出完整管理命令，也不能借机器人账号执行群管理动作。"""
    client = FakeConnectorClient()
    registry = ToolRegistry()
    register_test_tool(registry, "query_qq_group", QueryQqGroupTool(client))
    manage_tool = ManageQqGroupTool(
        client,
        GroupOperationApprovalRegistry(audit_path=str(tmp_path / "audit.jsonl")),
    )
    register_test_tool(registry, "manage_qq_group", manage_tool)
    agent = GroupOpsAgent(registry)
    context = build_context(
        "禁言10分钟",
        ["query_qq_group", "manage_qq_group"],
        mentions=["3969785168", "3807050597"],
        sender_id="2597164807",
    )

    result = asyncio.run(agent.run(context, "handle_group_ops"))

    assert result.status == "blocked"
    assert manage_tool.pending_for_event(context.event.event_id) is None
    assert client.operations == []
