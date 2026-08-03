from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping


def _text(value: Any) -> str:
    """把平台字段规范成可用于身份比较的单行文本。"""
    return " ".join(str(value or "").split())


def _mapping(value: Any) -> Mapping[str, Any]:
    """只接受字典形态的原始平台载荷，避免异常数据打断事件处理。"""
    return value if isinstance(value, Mapping) else {}


def canonical_message_identity(row: Mapping[str, Any], text: str = "") -> str:
    """生成跨重投稳定的消息身份。

    Event Center 每次接收 Webhook 都可能生成新的 eventId，因此 eventId 只能作为
    最后兜底。平台消息 ID 需要与会话范围组合，避免不同私聊或群聊碰巧使用相同 ID。
    """
    raw = _mapping(row.get("rawPayload") or row.get("raw_payload"))
    platform = _text(row.get("platform") or raw.get("platform") or "unknown").lower()
    chat_type = _text(row.get("chatType") or row.get("chat_type") or raw.get("message_type") or "unknown").lower()
    chat_id = _text(
        row.get("chatId")
        or row.get("chat_id")
        or raw.get("group_id")
        or raw.get("user_id")
        or "unknown"
    )
    scope = f"{platform}:{chat_type}:{chat_id}"

    platform_message_id = _text(
        row.get("platformMessageId")
        or row.get("platform_message_id")
        or raw.get("message_id")
        or raw.get("messageId")
        or raw.get("real_id")
    )
    if platform_message_id:
        return f"{scope}:platform:{platform_message_id}"

    client_message_id = _text(
        row.get("clientMessageId")
        or row.get("client_message_id")
        or raw.get("client_message_id")
        or raw.get("clientMessageId")
    )
    if client_message_id:
        return f"{scope}:client:{client_message_id}"

    # NapCat 的同一条消息可能被 Webhook 或 MQ 重投，每次进入 Event Center 时都会生成
    # 不同 eventId。只要平台时间、发送者、序列号和正文仍一致，就必须得到同一身份。
    at = _text(
        row.get("at")
        or row.get("sentAt")
        or row.get("timestamp")
        or row.get("receivedAt")
        or row.get("importedAt")
        or raw.get("time")
        or raw.get("timestamp")
    )
    sender = _mapping(row.get("sender"))
    sender_id = _text(
        sender.get("id")
        or sender.get("userId")
        or sender.get("user_id")
        or raw.get("user_id")
    )
    sequence = _text(
        row.get("sequence")
        or raw.get("message_seq")
        or raw.get("real_seq")
        or raw.get("real_id")
    )
    message_text = _text(
        text
        or row.get("text")
        or raw.get("raw_message")
        or raw.get("message")
    )
    segments = row.get("segments") or raw.get("message") or raw.get("segments") or []
    try:
        segment_text = json.dumps(segments, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        segment_text = _text(segments)

    # 至少存在平台时间或平台序列号时才使用内容指纹。否则两条真正独立但文本相同的
    # 消息可能被错误合并，此时宁可退回内部 eventId。
    if at or sequence:
        raw_identity = "|".join(
            (
                scope,
                at,
                sequence,
                sender_id,
                _text(row.get("direction") or raw.get("direction")),
                _text(row.get("actorType") or row.get("actor_type") or raw.get("actorType")),
                _text(row.get("messageOrigin") or row.get("origin") or raw.get("messageOrigin")),
                message_text,
                segment_text,
            )
        )
        digest = hashlib.sha1(raw_identity.encode("utf-8")).hexdigest()[:20]
        return f"{scope}:synthetic:{digest}"

    event_id = _text(row.get("eventId") or row.get("event_id") or row.get("id"))
    if event_id:
        return f"{scope}:event:{event_id}"

    raw_identity = "|".join((scope, sender_id, message_text, segment_text))
    return f"{scope}:synthetic:{hashlib.sha1(raw_identity.encode('utf-8')).hexdigest()[:20]}"


def is_runtime_generated_message(row: Mapping[str, Any]) -> bool:
    """根据身份元数据识别本 Runtime 发出的消息回显，不分析聊天正文。"""
    raw = _mapping(row.get("rawPayload") or row.get("raw_payload"))
    direction = _text(row.get("direction") or raw.get("direction")).upper()
    actor = _text(row.get("actorType") or row.get("actor_type") or raw.get("actorType")).upper()
    origin = _text(row.get("messageOrigin") or row.get("origin") or raw.get("messageOrigin")).upper()
    if direction in {"OUTBOUND", "SENT", "SELF", "TO_CONTACT"}:
        return True
    if actor in {"AGENT", "PROXY", "BOT", "SELF", "ME", "ACCOUNT_OWNER"}:
        return True
    if origin in {"AGENT", "AGENT_REPLY", "AGENT_AUTO", "AGENT_CONFIRMED", "PROXY_REPLY", "BOT"}:
        return True

    sender = _mapping(row.get("sender"))
    sender_id = _text(sender.get("id") or sender.get("user_id") or raw.get("user_id"))
    self_id = _text(row.get("selfId") or row.get("self_id") or raw.get("self_id"))
    if sender_id and self_id and sender_id == self_id:
        return True

    # Runtime 写回时使用受控命名空间生成 clientMessageId。平台回显即使缺少
    # direction，也会携带该标记，因此不能再次被当成联系人入站消息。
    client_message_id = _text(
        row.get("clientMessageId")
        or row.get("client_message_id")
        or raw.get("client_message_id")
        or raw.get("clientMessageId")
    )
    return client_message_id.startswith("runtime:")
