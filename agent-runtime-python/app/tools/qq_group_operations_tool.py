from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from secrets import token_urlsafe
from typing import Any

from app.clients.connector_service import ConnectorServiceClient


READ_ACTIONS = {
    "group_info",
    "member_list",
    "notice_list",
    "shut_list",
    "essence_list",
    "file_list",
}

MUTATING_ACTION_RISK = {
    "mute_member": "MEDIUM",
    "unmute_member": "MEDIUM",
    "whole_mute": "HIGH",
    "set_member_card": "MEDIUM",
    "set_group_name": "HIGH",
    "publish_notice": "HIGH",
    "set_essence": "MEDIUM",
    "delete_essence": "MEDIUM",
    "kick_member": "HIGH",
    "set_admin": "HIGH",
}


@dataclass
class GroupOperationProposal:
    """保存一次待审批动作；原始令牌只留在 Runtime 内存中，且只能消费一次。"""

    token: str
    action: str
    operation: dict[str, Any]
    risk: str
    event_id: str
    requester_id: str
    confirmation_phrase: str
    expires_at: str


class GroupOperationApprovalRegistry:
    """管理短期待审批单，并把准备、执行、拒绝写入本地审计日志。"""

    def __init__(self, ttl_seconds: int = 300, audit_path: str | None = None) -> None:
        self.ttl_seconds = max(30, ttl_seconds)
        self._proposals: dict[str, GroupOperationProposal] = {}
        default_path = Path("data") / "group-operations-audit.jsonl"
        self.audit_path = Path(audit_path or os.getenv("GROUP_OPS_AUDIT_PATH") or default_path)

    def create(
        self,
        action: str,
        operation: dict[str, Any],
        event_id: str,
        requester_id: str,
    ) -> GroupOperationProposal:
        """创建不可预测的一次性令牌，并按风险等级生成确认短语。"""
        self._remove_expired()
        token = token_urlsafe(24)
        risk = MUTATING_ACTION_RISK[action]
        target = operation.get("targetUserId") or ""
        if risk == "HIGH":
            confirmation_phrase = f"确认执行 {action} {operation['groupId']} {target}".strip()
        else:
            confirmation_phrase = "确认执行"
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=self.ttl_seconds)
        proposal = GroupOperationProposal(
            token=token,
            action=action,
            operation=operation,
            risk=risk,
            event_id=event_id,
            requester_id=requester_id,
            confirmation_phrase=confirmation_phrase,
            expires_at=expires_at.isoformat(),
        )
        self._proposals[token] = proposal
        self._audit("PREPARED", proposal)
        return proposal

    def consume(self, token: str, confirmation_text: str) -> GroupOperationProposal:
        """校验有效期和确认短语后消费令牌，失败时不会执行平台动作。"""
        self._remove_expired()
        proposal = self._proposals.get(token)
        if proposal is None:
            raise ValueError("审批单不存在、已过期或已被使用")
        if confirmation_text.strip() != proposal.confirmation_phrase:
            self._audit("CONFIRMATION_REJECTED", proposal)
            raise ValueError("确认短语不匹配")
        del self._proposals[token]
        return proposal

    def find_by_event_id(self, event_id: str) -> GroupOperationProposal | None:
        """按事件读取仍有效的审批单；令牌只在 Runtime 内部返回给执行工具。"""
        self._remove_expired()
        matches = [proposal for proposal in self._proposals.values() if proposal.event_id == event_id]
        return matches[-1] if matches else None

    def record_result(self, proposal: GroupOperationProposal, result: dict[str, Any]) -> None:
        """记录平台实际执行结果，便于追踪谁在何时批准了什么动作。"""
        status = "EXECUTED" if result.get("status") == "ok" else "EXECUTION_FAILED"
        self._audit(status, proposal, {"platformResult": result})

    def _remove_expired(self) -> None:
        """删除过期审批单并留下过期审计记录。"""
        now = datetime.now(timezone.utc)
        expired = [
            token
            for token, proposal in self._proposals.items()
            if datetime.fromisoformat(proposal.expires_at) <= now
        ]
        for token in expired:
            proposal = self._proposals.pop(token)
            self._audit("EXPIRED", proposal)

    def _audit(
        self,
        status: str,
        proposal: GroupOperationProposal,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """以 JSON Lines 追加审计记录；日志中只保存令牌指纹，不保存原始令牌。"""
        proposal_payload = asdict(proposal)
        raw_token = proposal_payload.pop("token")
        proposal_payload["approvalId"] = sha256(raw_token.encode("utf-8")).hexdigest()[:16]
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": status,
            "proposal": proposal_payload,
            **(extra or {}),
        }
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        with self.audit_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False) + "\n")


class QueryQqGroupTool:
    """低风险群资料查询适配器；只读动作可以进入普通工具白名单。"""

    name = "query_qq_group"

    def __init__(self, client: ConnectorServiceClient) -> None:
        self.client = client

    async def query(self, *, action: str, group_id: int) -> dict[str, Any]:
        """校验只读动作和群号后调用 Connector。"""
        action = str(action or "").strip()
        group_id = int(group_id or 0)
        if action not in READ_ACTIONS:
            raise ValueError(f"Unsupported read-only group action: {action}")
        if group_id <= 0:
            raise ValueError("group_id must be positive")
        return await self.client.query_group(action, group_id)


class ManageQqGroupTool:
    """群管理底层适配器；只生成审批单，实际执行仍需人工确认。"""

    name = "manage_qq_group"

    def __init__(
        self,
        client: ConnectorServiceClient,
        approvals: GroupOperationApprovalRegistry | None = None,
    ) -> None:
        self.client = client
        self.approvals = approvals or GroupOperationApprovalRegistry()

    async def prepare(self, **kwargs: Any) -> dict[str, Any]:
        """规范化动作参数并创建待人工审批单，不直接执行。"""
        action = str(kwargs.pop("action", "")).strip()
        event_id = str(kwargs.pop("event_id", "")).strip()
        requester_id = str(kwargs.pop("requester_id", "")).strip()
        if action not in MUTATING_ACTION_RISK:
            raise ValueError(f"Unsupported mutating group action: {action}")
        operation = self._normalize_operation(action, kwargs)
        proposal = self.approvals.create(action, operation, event_id, requester_id)
        return self._public_proposal(proposal)

    async def _approve_token(self, approval_token: str, confirmation_text: str) -> dict[str, Any]:
        """消费审批令牌后执行一次动作；重复提交同一令牌会被拒绝。"""
        proposal = self.approvals.consume(approval_token, confirmation_text)
        result = await self.client.execute_group_operation(proposal.operation)
        self.approvals.record_result(proposal, result)
        return {
            "status": "success" if result.get("status") == "ok" else "failed",
            "action": proposal.action,
            "risk": proposal.risk,
            "platformResult": result,
        }

    def pending_for_event(self, event_id: str) -> dict[str, Any] | None:
        """返回不含令牌的审批摘要，供 Event Center 展示。"""
        proposal = self.approvals.find_by_event_id(event_id)
        if proposal is None:
            return None
        return self._public_proposal(proposal)

    async def approve_event(self, event_id: str, confirmation_text: str) -> dict[str, Any]:
        """按事件定位内部令牌并执行审批，避免令牌经过桌面客户端。"""
        proposal = self.approvals.find_by_event_id(event_id)
        if proposal is None:
            raise ValueError("审批单不存在、已过期或已被使用")
        return await self._approve_token(proposal.token, confirmation_text)

    @staticmethod
    def _public_proposal(proposal: GroupOperationProposal) -> dict[str, Any]:
        """把内部审批对象转换成无令牌展示模型，供 Agent 和 Event Center 安全传递。"""
        return {
            "status": "confirmation_required",
            "eventId": proposal.event_id,
            "action": proposal.action,
            "risk": proposal.risk,
            "confirmationPhrase": proposal.confirmation_phrase,
            "expiresAt": proposal.expires_at,
            "operation": proposal.operation,
        }

    @staticmethod
    def _normalize_operation(action: str, values: dict[str, Any]) -> dict[str, Any]:
        """只保留 Connector DTO 明确支持的字段，丢弃模型附带的未知参数。"""
        group_id = int(values.get("group_id") or 0)
        if group_id <= 0:
            raise ValueError("group_id must be positive")
        operation: dict[str, Any] = {"action": action, "groupId": group_id}
        mappings = {
            "target_user_id": "targetUserId",
            "duration_seconds": "durationSeconds",
            "text": "text",
            "enable": "enable",
            "message_id": "messageId",
            "reject_add_request": "rejectAddRequest",
        }
        for source, target in mappings.items():
            value = values.get(source)
            if value is not None and value != "":
                operation[target] = value
        ManageQqGroupTool._validate_operation(action, operation)
        return operation

    @staticmethod
    def _validate_operation(action: str, operation: dict[str, Any]) -> None:
        """在创建审批单前验证必需参数，避免用户批准后才发现请求无效。"""
        target_actions = {
            "mute_member",
            "unmute_member",
            "set_member_card",
            "kick_member",
            "set_admin",
        }
        if action in target_actions and int(operation.get("targetUserId") or 0) <= 0:
            raise ValueError(f"{action} requires a positive target_user_id")

        if action == "mute_member":
            duration = int(operation.get("durationSeconds") or 0)
            if duration <= 0 or duration > 2_592_000:
                raise ValueError("mute_member duration_seconds must be between 1 and 2592000")

        if action in {"whole_mute", "set_admin"} and not isinstance(operation.get("enable"), bool):
            raise ValueError(f"{action} requires a boolean enable value")

        if action in {"set_member_card", "set_group_name", "publish_notice"}:
            text = str(operation.get("text") or "").strip()
            if not text:
                raise ValueError(f"{action} requires non-empty text")
            if len(text) > 3000:
                raise ValueError(f"{action} text is too long")

        if action in {"set_essence", "delete_essence"} and int(operation.get("messageId") or 0) <= 0:
            raise ValueError(f"{action} requires a positive message_id")
