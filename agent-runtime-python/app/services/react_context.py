from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable

from app.schemas.react_protocol import CandidateReplyGuardResult, ModelWorkingContext


class MasterTaskContextBuilder:
    """为主控台委托任务构建可安全交给 ReAct 模型的上下文。"""

    _CONTROL_KEYS = {
        "id", "userId", "user_id", "chatId", "chat_id", "accountId", "account_id",
        "platform", "targetQuery", "target_query", "targetName", "target_name",
        "stateJson", "state_json", "rawPayload", "toolArguments", "tool_arguments",
    }

    def build(
        self,
        *,
        task: dict[str, Any],
        timeline: list[dict[str, Any]],
        pre_task_history: list[dict[str, Any]],
        previous_state: dict[str, Any],
        task_created_at: str,
        current_time: datetime,
        resolved_time_text: str,
        history_access_allowed: bool,
        available_tools: Iterable[str],
    ) -> ModelWorkingContext:
        """提取任务事实、会话证据和有限记忆，隔离执行控制数据。"""
        # 联系人检索名和内部任务 ID 仅用于工具定位，不能作为聊天内容提示给模型。
        # 这样无需维护“禁止出现某个昵称”的静态词表，也能减少内部字段泄漏。
        internal_terms = self.internal_terms(task)
        return ModelWorkingContext(
            task_goal=self._sanitize_task_text(self._read_text(task, "objective"), internal_terms),
            success_criteria=self._sanitize_task_text(
                self._read_text(task, "successCriteria", "success_criteria"), internal_terms
            ),
            deadline_text=self._sanitize_task_text(
                self._read_text(task, "deadlineText", "deadline_text"), internal_terms
            ),
            task_created_at=task_created_at,
            current_time=current_time.isoformat(),
            resolved_time_text=resolved_time_text,
            conversation_timeline=self._normalize_messages(timeline, limit=500),
            pre_task_context=self._normalize_messages(pre_task_history, limit=30),
            working_memory=self._safe_memory(previous_state.get("workingMemory")),
            action_ledger=self._safe_action_ledger(previous_state.get("actionLedger")),
            history_access_allowed=history_access_allowed,
            available_tools=sorted({str(item).strip() for item in available_tools if str(item).strip()}),
        )

    def internal_terms(self, task: dict[str, Any]) -> tuple[str, ...]:
        """返回仅用于控制和会话定位、绝不应作为聊天气泡输出的内部术语。"""
        values = (task.get("targetName"), task.get("target_name"), task.get("targetQuery"), task.get("target_query"))
        return tuple(text for value in values if len(text := " ".join(str(value or "").split())) >= 2)

    def _safe_memory(self, value: Any) -> dict[str, Any]:
        """保留有助于持续推理的工作记忆，并丢弃所有控制面字段。"""
        if not isinstance(value, dict):
            return {}
        return {str(key): item for key, item in value.items() if str(key) not in self._CONTROL_KEYS}

    @staticmethod
    def _sanitize_task_text(text: str, internal_terms: Iterable[str]) -> str:
        """将控制台用于定位联系人的词替换为中性指代，避免污染对外聊天语境。"""
        sanitized = str(text or "")
        for term in sorted({str(item).strip() for item in internal_terms if str(item).strip()}, key=len, reverse=True):
            sanitized = sanitized.replace(term, "对方")
        return " ".join(sanitized.split())

    @staticmethod
    def _read_text(source: dict[str, Any], *keys: str) -> str:
        """从兼容 camelCase 与 snake_case 的字段中读取一段任务文本。"""
        for key in keys:
            value = " ".join(str(source.get(key) or "").split())
            if value:
                return value
        return ""


    def _normalize_messages(self, rows: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
        """把任务上下文整理成模型可读的聊天时间线。

        这里必须保留时间戳、说话人、方向和来源。Agent 需要知道“谁在什么时候说了什么”，
        才能判断自己是否已经发过消息、对方是否已经回复、任务是否已经推进到可结束状态。
        """
        result: list[dict[str, Any]] = []
        for row in rows[-limit:]:
            if not isinstance(row, dict):
                continue

            # 主控台输入的是任务指令，不是目标会话里的真实聊天内容，不能混入对话上下文。
            origin = self._read_row_text(row, "messageOrigin", "origin").upper()
            if origin in {"USER_COMMAND", "DESKTOP_COMMAND", "WORKSPACE_COMMAND"}:
                continue

            text = self._read_row_text(row, "text", "content", "rawMessage", "raw_message")
            if not text:
                continue

            role = self._normalize_role(row)
            speaker = self._read_row_text(row, "speaker", "senderName", "sender", "nickname", "displayName")
            if not speaker or speaker.lower() == "unknown":
                speaker = role

            result.append({
                "at": self._read_row_text(row, "at", "sentAt", "timestamp", "receivedAt"),
                "role": role,
                "speaker": speaker,
                "direction": self._read_row_text(row, "direction"),
                "actorType": self._read_row_text(row, "actorType", "actor_type"),
                "messageOrigin": self._read_row_text(row, "messageOrigin", "origin"),
                "eventId": self._read_row_text(
                    row,
                    "eventId",
                    "event_id",
                    "platformMessageId",
                    "platform_message_id",
                    "clientMessageId",
                    "client_message_id",
                    "id",
                ),
                "platformMessageId": self._read_row_text(row, "platformMessageId", "platform_message_id"),
                "clientMessageId": self._read_row_text(row, "clientMessageId", "client_message_id"),
                "text": text,
            })
        return result

    def _safe_action_ledger(self, value: Any) -> list[dict[str, Any]]:
        """保留最近工具动作账本，避免 Agent 忘记自己刚刚已经发送或结束过任务。"""
        if not isinstance(value, list):
            return []

        result: list[dict[str, Any]] = []
        allowed_keys = {
            "at", "tool", "action", "status", "message", "candidateMessage",
            # 模型只需要知道“做过什么”和“为什么做”，不应该看到联系人定位字段。
            # 联系人名称、QQ 号等路由信息留在服务端状态里，避免候选回复泄露内部称呼。
            "reason", "eventId",
        }
        for row in value[-80:]:
            if not isinstance(row, dict):
                continue
            item: dict[str, Any] = {}
            for key in allowed_keys:
                raw = row.get(key)
                if raw is None:
                    continue
                text = " ".join(str(raw).split())
                if not text:
                    continue
                item[key] = text[:500] if key in {"message", "candidateMessage", "reason"} else text[:120]
            if item:
                result.append(item)
        return result

    @staticmethod
    def _read_row_text(source: dict[str, Any], *keys: str) -> str:
        """从消息行里读取字段并做轻量清洗，统一处理空值、换行和多余空格。"""
        for key in keys:
            value = " ".join(str(source.get(key) or "").split())
            if value:
                return value
        return ""

    def _normalize_role(self, row: dict[str, Any]) -> str:
        """把平台方向字段归一成人能理解的角色：我方、对方、代理或系统。"""
        explicit_role = self._read_row_text(row, "role").strip().lower()
        if explicit_role in {"我方", "我", "本人", "账号主人", "self", "owner", "me", "user", "account_owner"}:
            return "我方"
        if explicit_role in {"对方", "联系人", "peer", "contact", "external", "other"}:
            return "对方"
        if explicit_role in {"代理", "agent", "bot", "proxy"}:
            return "代理"
        if explicit_role in {"系统", "system", "tool", "workflow"}:
            return "系统"

        origin = self._read_row_text(row, "messageOrigin", "origin").upper()
        direction = self._read_row_text(row, "direction").upper()
        actor_type = self._read_row_text(row, "actorType", "actor_type").upper()
        speaker = self._read_row_text(row, "speaker", "senderName", "sender", "nickname", "displayName").strip().lower()

        if origin in {"AGENT", "AGENT_REPLY", "PROXY_REPLY", "BOT"} or actor_type in {"AGENT", "PROXY", "BOT"}:
            return "代理"
        if origin in {"SYSTEM", "TOOL", "WORKFLOW"} or actor_type in {"SYSTEM", "TOOL"}:
            return "系统"
        if direction in {"OUTBOUND", "SENT", "SELF", "TO_CONTACT"} or actor_type in {"SELF", "USER", "OWNER", "ME", "ACCOUNT_OWNER"}:
            return "我方"
        if direction in {"INBOUND", "RECEIVED", "FROM_CONTACT"} or actor_type in {"PEER", "CONTACT", "OTHER", "MEMBER"}:
            return "对方"
        if speaker in {"我方", "我", "本人", "账号主人", "self", "owner", "me"}:
            return "我方"
        if speaker in {"对方", "联系人", "peer", "contact", "external"}:
            return "对方"
        if speaker in {"代理", "agent", "bot", "proxy"}:
            return "代理"
        if speaker in {"系统", "system", "tool"}:
            return "系统"
        return "对方"


class CandidateReplyGuard:
    """阻止模型把主控台内部定位信息直接写入对外候选回复。"""

    def validate(self, content: str, internal_terms: Iterable[str]) -> CandidateReplyGuardResult:
        """检查候选内容是否包含动态提取的控制术语，而非维护硬编码禁词表。"""
        normalized = " ".join(str(content or "").split())
        leaked = tuple(term for term in {" ".join(str(item or "").split()) for item in internal_terms} if len(term) >= 2 and term in normalized)
        if leaked:
            return CandidateReplyGuardResult(False, tuple(f"候选回复包含内部会话定位术语：{term}" for term in leaked))
        return CandidateReplyGuardResult(True)
