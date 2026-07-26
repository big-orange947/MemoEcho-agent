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
            history_access_allowed=history_access_allowed,
            available_tools=sorted({str(item).strip() for item in available_tools if str(item).strip()}),
        )

    def internal_terms(self, task: dict[str, Any]) -> tuple[str, ...]:
        """返回仅用于控制和会话定位、绝不应作为聊天气泡输出的内部术语。"""
        values = (task.get("targetName"), task.get("target_name"), task.get("targetQuery"), task.get("target_query"))
        return tuple(text for value in values if len(text := " ".join(str(value or "").split())) >= 2)

    def _normalize_messages(self, rows: list[dict[str, Any]], *, limit: int) -> list[dict[str, str]]:
        """将消息压缩为时间、角色和文本，避免将原始平台载荷传给模型。"""
        result: list[dict[str, str]] = []
        for row in rows[-limit:]:
            if not isinstance(row, dict):
                continue
            text = " ".join(str(row.get("text") or "").split())
            if text:
                result.append({
                    "at": " ".join(str(row.get("at") or row.get("sentAt") or "").split()),
                    "speaker": " ".join(str(row.get("speaker") or "上下文参与者").split()),
                    "text": text,
                })
        return result

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


class CandidateReplyGuard:
    """阻止模型把主控台内部定位信息直接写入对外候选回复。"""

    def validate(self, content: str, internal_terms: Iterable[str]) -> CandidateReplyGuardResult:
        """检查候选内容是否包含动态提取的控制术语，而非维护硬编码禁词表。"""
        normalized = " ".join(str(content or "").split())
        leaked = tuple(term for term in {" ".join(str(item or "").split()) for item in internal_terms} if len(term) >= 2 and term in normalized)
        if leaked:
            return CandidateReplyGuardResult(False, tuple(f"候选回复包含内部会话定位术语：{term}" for term in leaked))
        return CandidateReplyGuardResult(True)
