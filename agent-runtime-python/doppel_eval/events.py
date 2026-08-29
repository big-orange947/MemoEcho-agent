"""Deterministic synthetic UnifiedEvent construction for replay/load tests.

The field semantics below MUST match the runtime's own judgement rules
(reviewed from ``app/memory/manager.py``):

- ``role == "self"`` means the message was sent from the account's own QQ
  number; ``message_origin`` then distinguishes a human manual send
  (``USER_MANUAL``) from an agent-generated one (``AGENT_AUTO`` /
  ``AGENT_CONFIRMED``).  ``actor_type`` is the strongest signal:
  ``OWNER``/``AGENT``/``CONTACT``/``SYSTEM``.
- Fact authority rule: actor_type ``AGENT`` -> ``agent_output``;
  role ``self`` with agent origins -> ``agent_output``; role ``self``
  otherwise -> ``human_self``; everything else -> ``peer_statement``.
- Message identity: ``clientMessageId`` > ``platformMessageId`` >
  ``correlationId`` fallback.  Synthetic events always carry both client
  and platform IDs so replay idempotence is observable.
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime, timedelta

_SEQUENCE = itertools.count(1)


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "+00:00")


class SyntheticEvent:
    """Builder for a UnifiedEvent-compatible payload dict."""

    _slot: int

    def __init__(
        self,
        *,
        case_id: str,
        seq: int,
        self_id: str,
        chat_type: str,
        chat_id: str,
        sender_id: str,
        sender_name: str,
        text: str | None,
        at: datetime,
        actor_type: str,
        role: str,
        message_origin: str,
        direction: str,
        platform_message_id: str,
        client_message_id: str,
        scene: str | None = None,
        event_type: str = "message",
        attachments: list[dict] | None = None,
        mentions: list[str] | None = None,
    ) -> None:
        self._slot = case_id
        self._case_id = case_id
        self._seq = seq
        self._payload = {
            "eventId": f"syn:{case_id}:event:{seq}",
            "platform": "qq",
            "scene": scene or ("group" if chat_type == "group" else "private"),
            "eventType": event_type,
            "chatType": chat_type,
            "chatId": chat_id,
            "selfId": self_id,
            "sender": {"id": sender_id, "name": sender_name, "role": role},
            "text": text,
            "attachments": attachments or [],
            "mentions": mentions or [],
            "timestamp": _iso(at),
            "rawPayload": {},
            "actorType": actor_type,
            "platformMessageId": platform_message_id,
            "clientMessageId": client_message_id,
            "correlationId": None,
            "sequence": seq,
            "sentAt": _iso(at),
            "receivedAt": _iso(at),
            "importedAt": _iso(at),
            "direction": direction,
            "delegatedTaskId": None,
        }

    @property
    def case_id(self) -> str:
        return self._case_id

    @property
    def seq(self) -> int:
        return self._seq

    @property
    def payload(self) -> dict:
        return self._payload

    @property
    def message_id(self) -> str:
        """Stable logical message id for expectations and dedupe checks."""
        return f"{self._case_id}:m{self._seq}"


class SceneClock:
    """Deterministic absolute timestamps for one scene."""

    def __init__(self, base: datetime) -> None:
        self._base = base

    def at(self, offset_minutes: int) -> datetime:
        return self._base + timedelta(minutes=offset_minutes)


class EventFactory:
    """Small fluent wrapper producing the four speaker classes."""

    def __init__(
        self,
        *,
        self_id: str,
        clock: SceneClock,
        case_id: str,
        seq: int = 0,
    ) -> None:
        self._self_id = self_id
        self._clock = clock
        self._case_id = case_id
        self._seq = seq

    def _msg(
        self,
        *,
        chat_type: str,
        chat_id: str,
        text: str | None,
        offset_minutes: int,
        sender_id: str,
        sender_name: str,
        role: str,
        actor_type: str,
        message_origin: str,
        direction: str,
        event_type: str = "message",
    ) -> SyntheticEvent:
        self._seq += 1
        event = SyntheticEvent(
            case_id=self._case_id,
            seq=self._seq,
            self_id=self._self_id,
            chat_type=chat_type,
            chat_id=chat_id,
            sender_id=sender_id,
            sender_name=sender_name,
            text=text,
            at=self._clock.at(offset_minutes),
            actor_type=actor_type,
            role=role,
            message_origin=message_origin,
            direction=direction,
            platform_message_id=f"platform-{self._case_id}-{self._seq}",
            client_message_id=f"client-{self._case_id}-{self._seq}",
            event_type=event_type,
        )
        return event

    def owner(
        self,
        text: str,
        *,
        chat_type: str = "private",
        chat_id: str = "contact-001",
        offset: int,
    ) -> SyntheticEvent:
        return self._msg(
            chat_type=chat_type,
            chat_id=chat_id,
            text=text,
            offset_minutes=offset,
            sender_id=self._self_id,
            sender_name="号主",
            role="self",
            actor_type="OWNER",
            message_origin="USER_MANUAL",
            direction="out",
        )

    def agent_reply(
        self,
        text: str,
        *,
        chat_type: str = "private",
        chat_id: str = "contact-001",
        offset: int,
    ) -> SyntheticEvent:
        return self._msg(
            chat_type=chat_type,
            chat_id=chat_id,
            text=text,
            offset_minutes=offset,
            sender_id=self._self_id,
            sender_name="号主",
            role="self",
            actor_type="AGENT",
            message_origin="AGENT_AUTO",
            direction="out",
        )

    def contact(
        self,
        text: str,
        *,
        chat_type: str = "private",
        chat_id: str = "contact-001",
        offset: int,
        sender_id: str = "contact-001",
        sender_name: str = "联系人",
    ) -> SyntheticEvent:
        return self._msg(
            chat_type=chat_type,
            chat_id=chat_id,
            text=text,
            offset_minutes=offset,
            sender_id=sender_id,
            sender_name=sender_name,
            role="peer",
            actor_type="CONTACT",
            message_origin="",
            direction="in",
        )

    def group_member(
        self,
        text: str,
        *,
        chat_id: str = "group-001",
        offset: int,
        sender_id: str,
        sender_name: str,
    ) -> SyntheticEvent:
        return self._msg(
            chat_type="group",
            chat_id=chat_id,
            text=text,
            offset_minutes=offset,
            sender_id=sender_id,
            sender_name=sender_name,
            role="peer",
            actor_type="CONTACT",
            message_origin="",
            direction="in",
        )

    def system(self, text: str, *, chat_id: str, offset: int) -> SyntheticEvent:
        return self._msg(
            chat_type="group",
            chat_id=chat_id,
            text=text,
            offset_minutes=offset,
            sender_id="system",
            sender_name="系统",
            role="system",
            actor_type="SYSTEM",
            message_origin="",
            direction="in",
            event_type="system",
        )
