from __future__ import annotations

import hashlib
from typing import Any


class HistoryContextCompressor:
    """在上下文超出预算时压缩较早消息，同时完整保留最近对话。"""

    _SUMMARY_VERSION = 1
    _MAX_SUMMARY_CHARS = 900
    _MAX_SNIPPET_CHARS = 96

    def compress(
        self,
        messages: list[dict[str, Any]],
        max_messages: int,
        max_chars: int,
    ) -> list[dict[str, Any]]:
        """返回满足消息数和字符预算的上下文；未超限时保持原列表不变。"""
        if not messages:
            return []

        safe_message_limit = max(int(max_messages), 1)
        safe_char_limit = max(int(max_chars), 200)
        total_chars = sum(len(str(item.get("text") or "")) for item in messages)
        if len(messages) <= safe_message_limit and total_chars <= safe_char_limit:
            return messages
        if safe_message_limit == 1:
            return [self._truncate_message(messages[-1], safe_char_limit)]

        # 摘要最多占四分之一字符预算，剩余空间优先留给最近原文。
        summary_budget = min(
            self._MAX_SUMMARY_CHARS,
            max(160, safe_char_limit // 4),
        )
        exact_budget = max(1, safe_char_limit - summary_budget)
        recent_limit = safe_message_limit - 1
        recent_newest_first: list[dict[str, Any]] = []
        used_chars = 0

        for item in reversed(messages):
            if len(recent_newest_first) >= recent_limit:
                break
            text_length = len(str(item.get("text") or ""))
            remaining = exact_budget - used_chars
            if remaining <= 0:
                break
            if text_length > remaining:
                if recent_newest_first:
                    break
                recent_newest_first.append(self._truncate_message(item, remaining))
                used_chars += remaining
                break
            recent_newest_first.append(item)
            used_chars += text_length

        recent = list(reversed(recent_newest_first))
        older_count = len(messages) - len(recent)
        older = messages[:older_count]
        if not older:
            return recent

        summary = self._build_summary(older, min(summary_budget, safe_char_limit - used_chars))
        return ([summary] if summary else []) + recent

    def _build_summary(
        self,
        messages: list[dict[str, Any]],
        max_chars: int,
    ) -> dict[str, Any] | None:
        """把较早消息转换为低权威派生摘录，并保留全部可用来源事件 ID。"""
        if max_chars <= 0:
            return None

        source_event_ids = [
            str(item.get("eventId") or "").strip()
            for item in messages
            if str(item.get("eventId") or "").strip()
        ]
        fragments: list[str] = []
        used_chars = 0
        for item in messages:
            text = " ".join(str(item.get("text") or "").split()).strip()
            if not text:
                continue
            speaker = self._resolve_speaker(item)
            snippet = text[: self._MAX_SNIPPET_CHARS]
            fragment = f"{speaker}：{snippet}"
            separator_length = 3 if fragments else 0
            remaining = max_chars - used_chars - separator_length
            if remaining <= 0:
                break
            fragments.append(fragment[:remaining])
            used_chars += min(len(fragment), remaining) + separator_length

        if not fragments:
            return None

        first_timestamp = str(messages[0].get("timestamp") or "")
        last_timestamp = str(messages[-1].get("timestamp") or "")
        digest_source = "|".join(source_event_ids) or f"{first_timestamp}|{last_timestamp}|{len(messages)}"
        digest = hashlib.sha256(digest_source.encode("utf-8")).hexdigest()[:16]
        return {
            "eventId": f"derived-history:{digest}",
            "role": "summary",
            "senderName": "Memo Echo",
            "text": " / ".join(fragments)[:max_chars],
            "timestamp": last_timestamp,
            "messageOrigin": "DERIVED_SUMMARY",
            "actorType": "SYSTEM",
            "factAuthority": "derived_summary",
            "derivedSummary": True,
            "summaryVersion": self._SUMMARY_VERSION,
            "sourceEventIds": source_event_ids,
            "sourceCount": len(messages),
            "sourceTimeRange": {
                "start": first_timestamp,
                "end": last_timestamp,
            },
            "mediaAnalysis": [],
            "processingStatus": "",
            "needHumanConfirmation": False,
            "writeBackStatus": "",
        }

    @staticmethod
    def _resolve_speaker(item: dict[str, Any]) -> str:
        """根据统一身份和事实权威生成不会混淆双方的摘要说话方。"""
        authority = str(item.get("factAuthority") or "")
        actor_type = str(item.get("actorType") or "").upper()
        if authority == "agent_output" or actor_type == "AGENT":
            return "代理曾发送"
        if actor_type == "OWNER" or str(item.get("role") or "") == "self":
            return "我"
        if actor_type == "SYSTEM":
            return "系统"
        return "对方"

    @staticmethod
    def _truncate_message(item: dict[str, Any], max_chars: int) -> dict[str, Any]:
        """复制并截断单条超长近期消息，避免修改调用方持有的原始字典。"""
        result = dict(item)
        result["text"] = str(item.get("text") or "")[:max(max_chars, 0)]
        return result
