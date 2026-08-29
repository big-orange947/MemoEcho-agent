"""UnifiedEvent -> Doppel scope/message mapping (pure, dependency-free).

The actor rules here mirror the runtime's own judgement logic
(``app/memory/manager.py``):

- ``actor_type`` is the strongest signal: AGENT -> ``agent``,
  OWNER -> ``owner``, CONTACT -> ``contact``, SYSTEM -> ``system``.
- Without actor_type, ``role == self`` with ``message_origin`` in
  AGENT_AUTO/AGENT_CONFIRMED -> ``agent`` (an auto-reply from the
  account's own number is NOT an owner statement).
- ``role == self`` otherwise -> ``owner``; ``role == peer`` -> ``contact``.

Scope semantics: user_id is always the serviced owner's QQ account
(self_id); chat_id is the peer/group; the speaker is carried in
sender_id/message subject, never in the scope.

Safety: events without platform=qq or without self_id/chat_id cannot
form an exact scope.  They are reported as ``errors`` instead of being
silently merged into a ``qq:unknown`` namespace.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.schemas.events import UnifiedEvent

#: Stable agent namespace; never embed a model name here.
AGENT_ID = "memoecho-main"
ADAPTER_VERSION = "doppel-bridge/1"

#: content types normalized from segments/attachments.
_IMAGE_SEGMENTS = {"image", "img"}
_FILE_SEGMENTS = {"file", "record", "video", "audio", "voice", "rich"}
_STICKER_SEGMENTS = {"face", "mface", "sticker", "bface", "sface"}
_NUDGE_SEGMENTS = {"poke", "nudge", "vip_poke", "gift"}


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def resolve_actor(event: UnifiedEvent) -> str:
    """Return one of owner/contact/agent/system for a UnifiedEvent."""
    actor_type = str(event.actor_type or "").strip().upper()
    if actor_type:
        if actor_type == "AGENT":
            return "agent"
        if actor_type == "OWNER":
            return "owner"
        if actor_type == "CONTACT":
            return "contact"
        if actor_type == "SYSTEM":
            return "system"
    role = str((event.sender.role if event.sender else None) or "").strip().lower()
    if role == "self":
        origin = str(event.raw_payload.get("messageOrigin") or "").upper()
        if origin in {"AGENT_AUTO", "AGENT_CONFIRMED"}:
            return "agent"
        return "owner"
    if role == "peer":
        return "contact"
    if role == "system":
        return "system"
    # fallback: sender is the account itself -> owner
    if event.sender and event.self_id and event.sender.id == event.self_id:
        return "owner"
    return "contact"


def validate_event(event: UnifiedEvent) -> list[str]:
    """Return a list of reasons this event cannot form a safe Doppel scope."""
    errors: list[str] = []
    if str(event.platform or "").strip().lower() != "qq":
        errors.append(f"unsupported platform: {event.platform!r} (bridge requires qq)")
    if not str(event.self_id or "").strip():
        errors.append("missing self_id: cannot determine the serviced owner")
    if not str(event.chat_id or "").strip():
        errors.append("missing chat_id: cannot determine an exact conversation")
    return errors


def to_scope(event: UnifiedEvent) -> dict | None:
    """Doppel MemoryScope-shaped dict, or None when identity is unsafe."""
    if validate_event(event):
        return None
    self_id = str(event.self_id or "").strip()
    chat_id = str(event.chat_id or "").strip()
    chat_type = str(event.chat_type or "private").strip()
    platform = str(event.platform or "qq").strip()
    if chat_type == "group":
        scoped_chat_id = f"qq-group:{chat_id}"
    else:
        scoped_chat_id = f"qq:{chat_id}"
    return {
        "user_id": f"qq:{self_id}",
        "agent_id": AGENT_ID,
        "platform": platform,
        "chat_type": chat_type,
        "chat_id": scoped_chat_id,
        "extra_dimensions": {"tenant_id": f"qq-account:{self_id}"},
    }


def stable_message_id(event: UnifiedEvent) -> str:
    """Identity priority mirrors the runtime: client > platform > event id."""
    self_id = str(event.self_id or "").strip()
    chat_type = str(event.chat_type or "private").strip()
    chat_id = str(event.chat_id or "").strip()
    client_id = str(event.client_message_id or "").strip()
    platform_id = str(event.platform_message_id or "").strip()
    if client_id:
        leaf = f"message:{client_id}"
    elif platform_id:
        leaf = f"message:{platform_id}"
    else:
        leaf = f"event:{event.event_id}"
    return f"qq:{self_id}:{chat_type}:{chat_id}:{leaf}"


def stable_event_id(event: UnifiedEvent) -> str:
    return str(event.event_id or "").strip() or stable_message_id(event)


def _derive_content_type(event: UnifiedEvent) -> str:
    """Normalized content type from segments/attachments (not the platform event)."""
    if event.attachments:
        first_type = str(event.attachments[0].file_type or "").strip().lower()
        if first_type.startswith("image"):
            return "image"
        if first_type.startswith(("video", "audio", "voice")):
            return first_type.split("/")[0]
        return "file"
    for segment in event.segments:
        segment_type = str(segment.type or "").strip().lower()
        if segment_type in _IMAGE_SEGMENTS:
            return "image"
        if segment_type in _FILE_SEGMENTS:
            return "file"
        if segment_type in _STICKER_SEGMENTS:
            return "sticker"
        if segment_type in _NUDGE_SEGMENTS:
            return "nudge"
    return "text"


def _message_type(event: UnifiedEvent) -> str:
    event_type = str(event.event_type or "").strip()
    if event_type in {"message", "message_sent", ""}:
        return _derive_content_type(event)
    if event_type in {"notice", "request", "meta_event"}:
        return "system"
    return event_type or "message"


def _attachment_payload(attachment: Any) -> dict:
    file_type = str(attachment.file_type or "").strip()
    file_name = str(attachment.file_name or "").strip()
    file_id = str(attachment.file_id or "").strip()
    if not file_type:
        if file_name.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp")):
            file_type = "image"
        else:
            file_type = "file"
    return {
        "type": file_type,
        "file_id": file_id,
        "file_name": file_name,
        "url": str(attachment.url or "").strip(),
    }


def _media_ref(attachment: Any) -> dict:
    return {
        "media_id": str(attachment.file_id or "").strip(),
        "uri": str(attachment.url or "").strip(),
        "mime_type": str(attachment.file_type or "").strip(),
        "filename": str(attachment.file_name or "").strip(),
    }


def _part_from_attachment(attachment: Any) -> dict:
    file_type = str(attachment.file_type or "").strip().lower()
    if file_type.startswith("image"):
        part_type = "image"
    elif file_type.startswith(("audio", "voice")):
        part_type = "audio"
    elif file_type.startswith("video"):
        part_type = "video"
    else:
        part_type = "file"
    media = _media_ref(attachment)
    if not media["media_id"] and not media["uri"]:
        media = None
    return {
        "type": part_type,
        "text": "",
        "media": media,
        "metadata": {
            "source": "attachment",
            "file_type": file_type,
            "file_name": str(attachment.file_name or "").strip(),
        },
    }


def _parts_from_segments(event: UnifiedEvent) -> list[dict]:
    """Map MessageSegments onto Doppel ContentPart-shaped dicts."""
    parts: list[dict] = []
    for segment in event.segments:
        segment_type = str(segment.type or "").strip().lower()
        data = segment.data or {}
        if segment_type == "text":
            text = str(data.get("text", "") or "").strip()
            if text:
                parts.append(
                    {"type": "text", "text": text, "media": None, "metadata": {}}
                )
        elif segment_type in _IMAGE_SEGMENTS:
            parts.append(
                {
                    "type": "image",
                    "text": "",
                    "media": {
                        "media_id": str(
                            data.get("file_id") or data.get("file") or ""
                        ).strip(),
                        "uri": str(data.get("url") or "").strip(),
                        "filename": str(data.get("file_name") or "").strip(),
                    },
                    "metadata": {"segment": "image"},
                }
            )
        elif segment_type in {"at", "mention"}:
            parts.append(
                {
                    "type": "mention",
                    "text": "",
                    "media": None,
                    "metadata": {
                        "qq": str(data.get("qq") or "").strip(),
                        "name": str(data.get("name") or "").strip(),
                    },
                }
            )
        elif segment_type == "reply":
            parts.append(
                {
                    "type": "reply",
                    "text": "",
                    "media": None,
                    "metadata": {
                        "reply_id": str(
                            data.get("id") or data.get("message_id") or ""
                        ).strip(),
                    },
                }
            )
        elif segment_type in _STICKER_SEGMENTS:
            parts.append(
                {
                    "type": "sticker",
                    "text": "",
                    "media": None,
                    "metadata": {"id": str(data.get("id") or "").strip()},
                }
            )
        elif segment_type in _NUDGE_SEGMENTS:
            parts.append(
                {
                    "type": "interaction",
                    "text": "",
                    "media": None,
                    "metadata": {"kind": "nudge"},
                }
            )
        elif segment_type in _FILE_SEGMENTS:
            parts.append(
                {
                    "type": "file",
                    "text": "",
                    "media": {
                        "media_id": str(data.get("file_id") or "").strip(),
                        "uri": str(data.get("url") or "").strip(),
                        "filename": str(data.get("file_name") or "").strip(),
                    },
                    "metadata": {"segment": segment_type},
                }
            )
    return parts


def _append_attachment_parts(parts: list[dict], event: UnifiedEvent) -> list[dict]:
    """Project legacy attachments into ContentPart without duplicating segments."""
    seen: set[str] = set()

    def identities(media: dict, filename: str = "") -> set[str]:
        values: set[str] = set()
        if media.get("media_id"):
            values.add(f"id:{media['media_id']}")
        if media.get("uri"):
            values.add(f"uri:{media['uri']}")
        if not values and filename:
            values.add(f"name:{filename}")
        return values

    for part in parts:
        media = part.get("media") or {}
        seen.update(identities(media, str(media.get("filename") or "")))
    for attachment in event.attachments:
        candidate = _part_from_attachment(attachment)
        media = candidate.get("media") or {}
        candidate_ids = identities(
            media,
            str(media.get("filename") or candidate["metadata"].get("file_name") or ""),
        )
        if candidate_ids and candidate_ids & seen:
            continue
        parts.append(candidate)
        seen.update(candidate_ids)
    return parts


def _raw_provenance(event: UnifiedEvent) -> dict:
    """Whitelisted audit provenance; never stores the full raw payload."""
    raw_payload = event.raw_payload if isinstance(event.raw_payload, dict) else {}
    return {
        "platform": str(event.platform or "qq").strip(),
        "platform_event_type": str(event.event_type or "").strip(),
        "scene": str(event.scene or "").strip(),
        "actor_type": str(event.actor_type or "").strip(),
        "direction": str(event.direction or "").strip(),
        "message_origin": str(raw_payload.get("messageOrigin") or "").strip(),
        "correlation_id": str(event.correlation_id or "").strip(),
        "sequence": event.sequence,
        "mentions": [str(item) for item in (event.mentions or [])],
        "received_at": str(event.received_at or "").strip(),
        "imported_at": str(event.imported_at or "").strip(),
        "delegated_task_id": str(event.delegated_task_id or "").strip(),
        "adapter": ADAPTER_VERSION,
    }


def to_message(event: UnifiedEvent) -> dict:
    """Doppel ChatMessage-shaped dict (compatible text + provenance)."""
    at = _parse_time(event.sent_at or event.timestamp or event.received_at)
    text = str(event.text or "").strip()
    attachments = [_attachment_payload(item) for item in event.attachments]
    parts = _parts_from_segments(event)
    parts = _append_attachment_parts(parts, event)
    if not parts and text:
        parts.append({"type": "text", "text": text, "media": None, "metadata": {}})
    return {
        "actor": resolve_actor(event),
        "text": text,
        "at": at.isoformat() if at else None,
        "event_id": stable_event_id(event),
        "message_id": stable_message_id(event),
        "sender_id": str((event.sender.id if event.sender else "") or "").strip(),
        "message_type": _message_type(event),
        "attachments": attachments,
        "raw": _raw_provenance(event),
        "parts": parts,
    }


def bridge_payload(event: UnifiedEvent) -> dict:
    """One bridge call used by live, replay and synthetic paths.

    Returns errors when the event cannot form a safe scope; consumers
    should route such events to a dead-letter instead of a tenant namespace.
    """
    errors = validate_event(event)
    scope = to_scope(event) if not errors else None
    message = to_message(event)
    return {
        "scope": scope,
        "message": message,
        "actor": resolve_actor(event),
        "event_id": stable_event_id(event),
        "message_id": stable_message_id(event),
        "errors": errors,
    }
