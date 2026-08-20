from __future__ import annotations

"""委托任务的统一上下文构建。

该模块把 Java 保存的事件、实时 webhook 事件和任务状态统一成同一条时间线。
LangGraph 的各节点只消费该结果，不能再各自按时间或会话过滤，避免子任务丢失父任务消息。
"""

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Sequence


class DelegatedTaskContextAssembler:
    """负责为单个委托任务生成唯一、可追溯的上下文包。"""

    def assemble(
        self,
        *,
        event: dict[str, Any],
        task: dict[str, Any],
        task_history: Sequence[dict[str, Any]],
        pre_task_history: Sequence[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """按任务锚点、会话范围、消息身份构造供状态图直接消费的上下文。"""
        task_state = self._json_object(task.get("stateJson") or task.get("state_json"))
        scope = self._resolve_scope(event, task, task_state)
        anchor = self._resolve_anchor(task, task_state)
        rows = [*task_history, event]
        timeline = self._normalize_rows(rows, scope=scope, anchor=anchor, always_include=event)
        pre_history = self._normalize_rows(pre_task_history or [], scope=scope, anchor=None)
        return {
            "version": 1,
            "scope": scope,
            "taskStartedAt": anchor or "",
            "currentEventId": self._event_id(event),
            "taskTimeline": timeline,
            "preTaskHistory": pre_history,
            "taskState": task_state,
            "workflowFacts": task_state.get("workflowFacts") or task_state.get("facts") or {},
            "conversationMemory": task_state.get("conversationMemory") or {},
            "longTermMemory": task_state.get("longTermMemory") or {},
            "toolResults": task_state.get("toolResults") or [],
            "diagnostics": {
                "taskTimelineSize": len(timeline),
                "preTaskHistorySize": len(pre_history),
                "scope": scope,
            },
        }

    def _normalize_rows(
        self,
        rows: Sequence[dict[str, Any]],
        *,
        scope: dict[str, str],
        anchor: str | None,
        always_include: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """标准化、按范围过滤、去重并排序消息；当前事件永远不会被意外排除。"""
        normalized: dict[str, dict[str, Any]] = {}
        current_id = self._event_id(always_include or {})
        anchor_value = self._timestamp(anchor)
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            # 当前 webhook 是任务恢复的触发源，字段不完整时也必须进入上下文。
            is_current_source = row is always_include or (current_id and self._event_id(row) == current_id)
            if not is_current_source and not self._matches_scope(row, scope):
                continue
            item = self._normalize_row(row, index)
            is_current = item["eventId"] == current_id or is_current_source
            if anchor_value is not None and not is_current:
                row_time = self._timestamp(item["at"])
                if row_time is not None and row_time < anchor_value:
                    continue
            normalized[item["identityKey"]] = item
        return sorted(normalized.values(), key=lambda item: (item["at"], item["identityKey"]))

    def _normalize_row(self, row: dict[str, Any], index: int) -> dict[str, Any]:
        """将不同来源的事件转为带发送方和时间戳的统一消息格式。"""
        event_id = self._event_id(row) or self._fallback_id(row, index)
        at = self._first_text(row, "occurredAt", "occurred_at", "timestamp", "time", "createdAt", "created_at")
        text = self._message_text(row)
        role = self._role(row)
        return {
            "eventId": event_id,
            "identityKey": self._first_text(row, "identityKey", "canonicalIdentity") or event_id,
            "platformMessageId": self._first_text(row, "platformMessageId", "messageId", "message_id"),
            "clientMessageId": self._first_text(row, "clientMessageId", "client_message_id"),
            "at": self._normalize_time(at),
            "role": role,
            "speaker": self._speaker(row, role),
            "text": text,
            "eventType": self._first_text(row, "eventType", "event_type", "postType", "post_type") or "message",
            "direction": self._first_text(row, "direction"),
            "actorType": self._first_text(row, "actorType", "actor_type"),
            "messageOrigin": self._first_text(row, "messageOrigin", "message_origin", "origin"),
            "platform": self._first_text(row, "platform"),
            "chatType": self._chat_type(row),
            "chatId": self._conversation_id(row),
        }

    def _resolve_scope(self, event: dict[str, Any], task: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
        """会话范围以步骤固化值为准，其次才是事件与持久化状态。

        历史查询参数必须来自步骤的 conversationScope，而不是从当前事件临时推导，
        否则同一步骤在不同事件下可能被错误地归入不同会话。私聊 chatId 固定为对端平台账号。
        """
        step_scope = self._step_conversation_scope(task)
        if step_scope:
            return step_scope

        sources = (event, task, state)
        conversation_ids: list[str] = []
        for source in sources:
            for conversation_id in self._conversation_ids(source):
                if conversation_id not in conversation_ids:
                    conversation_ids.append(conversation_id)
        return {
            "platform": self._first_from(sources, "platform"),
            "chatType": next((self._chat_type(source) for source in sources if self._chat_type(source)), ""),
            # 私聊范围必须按对端账号匹配。NapCat 发出的私聊事件可能把 chatId 写成机器人自身，
            # 不能把该值作为唯一会话键，否则我方历史会被过滤掉。
            "chatId": conversation_ids[0] if conversation_ids else "",
            "conversationIds": conversation_ids,
        }

    def _step_conversation_scope(self, task: dict[str, Any]) -> dict[str, Any] | None:
        """从步骤固化的 conversationScopeJson 恢复会话范围；非法 JSON 时安全回退。"""
        raw = task.get("conversationScopeJson") or task.get("conversation_scope_json") or task.get("conversationScope")
        if not isinstance(raw, str) or not raw.strip():
            return None
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if not isinstance(parsed, dict):
            return None
        platform = str(parsed.get("platform") or "").strip()
        chat_type = self._chat_type(parsed)
        chat_id = str(parsed.get("chatId") or parsed.get("chat_id") or "").strip()
        if not (platform and chat_type and chat_id):
            return None
        return {
            "platform": platform,
            "chatType": chat_type,
            "chatId": chat_id,
            "conversationIds": [chat_id],
        }

    def _resolve_anchor(self, task: dict[str, Any], state: dict[str, Any]) -> str | None:
        """解析步骤起点水位，历史消息只从该时刻开始进入任务时间线。

        优先使用步骤启动时刻 startedAt（Java 创建/激活步骤时固化），
        其次才是任务创建时刻，确保 L1 只覆盖步骤真正开始后的证据。
        """
        for source in (task, state):
            value = self._first_text(source, "startedAt", "started_at", "taskStartedAt", "taskCreatedAt", "startAt", "createdAt", "created_at")
            if value:
                return self._normalize_time(value)
        return None

    def _matches_scope(self, row: dict[str, Any], scope: dict[str, Any]) -> bool:
        """仅排除明确属于其他会话的消息；字段缺失的存量事件保留以兼容旧数据。"""
        expected_platform = scope.get("platform", "")
        actual_platform = self._first_text(row, "platform")
        if expected_platform and actual_platform and expected_platform != actual_platform:
            return False
        expected_type = scope.get("chatType", "")
        actual_type = self._chat_type(row)
        if expected_type and actual_type and expected_type != actual_type:
            return False
        expected_ids = {str(value) for value in scope.get("conversationIds", []) if value}
        if not expected_ids and scope.get("chatId"):
            expected_ids.add(str(scope["chatId"]))
        actual_ids = set(self._conversation_ids(row, chat_type=actual_type))
        if expected_ids and actual_ids and expected_ids.isdisjoint(actual_ids):
            return False
        return True

    def _role(self, row: dict[str, Any]) -> str:
        """依据事件来源和方向明确区分代理、我方、对方和系统消息。"""
        origin = self._first_text(row, "messageOrigin", "message_origin", "origin", "actorType", "actor_type").upper()
        if any(marker in origin for marker in ("AGENT", "PROXY", "BOT")):
            return "代理"
        direction = self._first_text(row, "direction").lower()
        if row.get("isSelf") is True or row.get("is_self") is True or direction in {"outbound", "out", "send", "sent"}:
            return "我方"
        sender = row.get("sender") if isinstance(row.get("sender"), dict) else {}
        sender_id = self._first_text(sender, "userId", "user_id", "id")
        self_id = self._first_text(row, "selfId", "self_id", "accountId", "account_id")
        if sender_id and self_id and sender_id == self_id:
            return "我方"
        if direction in {"system", "internal"}:
            return "系统"
        return "对方"

    def _speaker(self, row: dict[str, Any], role: str) -> str:
        """提取展示名称，缺失时使用角色名而不泄漏内部备注。"""
        sender = row.get("sender") if isinstance(row.get("sender"), dict) else {}
        return self._first_text(row, "senderName", "sender_name", "nickname", "displayName") or self._first_text(sender, "card", "nickname", "name") or role

    def _message_text(self, row: dict[str, Any]) -> str:
        """提取文本内容，并为图片、文件等非文本消息提供稳定的摘要占位。"""
        text = self._first_text(row, "text", "content", "rawMessage", "raw_message", "message", "body")
        if text:
            return text
        segments = row.get("segments") or row.get("message")
        if isinstance(segments, list):
            kinds = {str(item.get("type", "")).lower() for item in segments if isinstance(item, dict)}
            if "image" in kinds:
                return "[图片]"
            if "file" in kinds:
                return "[文件]"
            if kinds & {"face", "mface", "emoji"}:
                return "[表情]"
        return "[非文本消息]"

    def _event_id(self, row: dict[str, Any]) -> str:
        """返回跨来源稳定的事件标识。"""
        return self._first_text(row, "eventId", "event_id", "id", "messageId", "message_id", "platformMessageId")

    def _fallback_id(self, row: dict[str, Any], index: int) -> str:
        """旧事件没有 ID 时使用内容摘要生成稳定去重键。"""
        digest = hashlib.sha1(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]
        return f"legacy:{index}:{digest}"

    def _first_from(self, sources: Sequence[dict[str, Any]], *keys: str) -> str:
        """从多个来源按优先级获取首个非空文本字段。"""
        for source in sources:
            value = self._first_text(source, *keys)
            if value:
                return value
        return ""

    def _chat_type(self, row: dict[str, Any]) -> str:
        """归一化 QQ/旧存量事件的群聊、私聊类型，避免 messageType 与 chatType 不一致。"""
        value = self._first_text(row, "chatType", "chat_type", "messageType", "message_type").lower()
        if value in {"private", "friend", "direct", "dm"}:
            return "private"
        if value in {"group", "guild", "channel"}:
            return "group"
        return value

    def _conversation_id(self, row: dict[str, Any], *, chat_type: str | None = None) -> str:
        """返回规范化会话 ID。

        NapCat 私聊事件通常没有 chatId，而是将对端 QQ 放在 userId；群聊则使用
        groupId。这里统一读取这些别名，避免真实上下文被错误过滤为空。
        """
        conversation_ids = self._conversation_ids(row, chat_type=chat_type)
        return conversation_ids[0] if conversation_ids else ""

    def _conversation_ids(self, row: dict[str, Any], *, chat_type: str | None = None) -> list[str]:
        """返回事件所属会话的候选 ID，并优先使用私聊对端而不是机器人自身。

        NapCat 的入站私聊通常把对端放在 ``userId``；出站私聊则可能把 ``chatId`` 写为
        ``selfId``。这里从 userId、peerId、targetUserId 和 sender 中提取对端身份，保证同一
        私聊的收发消息归入同一时间线。仅当没有对端信息时，才回退到原始 chatId。
        """
        direct = self._first_text(row, "chatId", "chat_id", "conversationId", "conversation_id")
        resolved_type = chat_type or self._chat_type(row)
        if resolved_type == "group":
            return self._distinct([direct, self._first_text(row, "groupId", "group_id")])
        if resolved_type == "private":
            sender = row.get("sender") if isinstance(row.get("sender"), dict) else {}
            self_ids = set(self._distinct([
                self._first_text(row, "selfId", "self_id", "botId", "bot_id", "accountId", "account_id"),
            ]))
            peer_candidates = self._distinct([
                self._first_text(row, "peerId", "peer_id", "targetUserId", "target_user_id", "userId", "user_id"),
                self._first_text(sender, "userId", "user_id", "id"),
            ])
            peers = [value for value in peer_candidates if value not in self_ids]
            return peers or self._distinct([direct])
        return self._distinct([direct, self._first_text(row, "groupId", "group_id", "userId", "user_id")])

    @staticmethod
    def _distinct(values: Sequence[str]) -> list[str]:
        """保留非空 ID 的原始顺序，避免多字段重复造成错误匹配。"""
        result: list[str] = []
        for value in values:
            if value and value not in result:
                result.append(value)
        return result

    @staticmethod
    def _first_text(source: dict[str, Any], *keys: str) -> str:
        """读取并规范化单个来源中的首个非空字段。"""
        for key in keys:
            value = source.get(key)
            if value is not None and str(value).strip():
                return str(value).strip()
        return ""

    @staticmethod
    def _json_object(value: Any) -> dict[str, Any]:
        """安全读取 stateJson，异常或非对象数据一律回退为空对象。"""
        if isinstance(value, dict):
            return value
        if not isinstance(value, str) or not value.strip():
            return {}
        try:
            loaded = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return loaded if isinstance(loaded, dict) else {}

    @staticmethod
    def _timestamp(value: str | None) -> float | None:
        """把 ISO 或 epoch 秒/毫秒转换为可比较的 UTC 时间戳。"""
        if not value:
            return None
        try:
            numeric = float(value)
            return numeric / 1000 if numeric > 10_000_000_000 else numeric
        except (TypeError, ValueError):
            pass
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None

    def _normalize_time(self, value: str | None) -> str:
        """保留可排序的 ISO 时间；未知时间使用空串并在排序时稳定处理。"""
        timestamp = self._timestamp(value)
        if timestamp is None:
            return value or ""
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()
