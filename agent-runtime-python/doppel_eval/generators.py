"""Tiered dataset generators for Doppel shadow evaluation.

Tiers:
- ``adapter``  ~50 events: knowledge scenes only.  Validates QQ event
  mapping correctness (fields/actors/direction) without any volume.
- ``quality``  ~250-400 events: knowledge scenes expanded with injected
  noise, random chit-chat and extra speakers.  For LLM extraction and
  consolidation quality runs.
- ``load``     N events (default 10k): pure conversation streams across
  many scopes for throughput/isolation tests with deterministic
  (non-LLM) pipelines only.

All tiers are deterministic under a fixed seed and produce
``UnifiedEvent``-compatible JSONL plus a manifest with fingerprint.
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path

from doppel_eval.events import EventFactory, SceneClock
from doppel_eval.scenarios import SELF_ID, Scene, build_all_scenes

#: Platform/sender pools for load generation (all synthetic, no QQ link).
_CONTACT_POOL = [
    ("contact-101", "老张"),
    ("contact-102", "小李"),
    ("contact-103", "阿紫"),
    ("contact-104", "王姐"),
    ("contact-105", "小陈"),
    ("contact-106", "大刘"),
]
_GROUP_POOL = ["group-201", "group-202", "group-203"]

_NOISE_PHRASES = [
    "哈哈",
    "好的",
    "嗯嗯",
    "在吗",
    "收到",
    "😂",
    "[表情]",
    "赞",
    "晚安",
    "打扰了",
]
_CHITCHAT = [
    "今天天气不错",
    "看了个电影还不错",
    "外卖到了我先吃饭",
    "周末打算去爬山",
    "这条消息不用回",
    "下班了",
    "刚开完会",
    "最近有点忙",
    "改天一起吃饭",
    "刚看到个新闻",
]

#: Deterministic fact templates for load-tier statements.
_OWNER_FACTS = [
    ("我住在{city}", "city"),
    ("我最近在学{skill}", "skill"),
    ("我养了一只{pet}，叫{pet_name}", "pet"),
    ("我喜欢吃{food}，讨厌{food_bad}", "food"),
    ("我在{company}上班", "company"),
]


class Tier(str, Enum):
    ADAPTER = "adapter"
    QUALITY = "quality"
    LOAD = "load"


@dataclass
class TierConfig:
    tier: Tier
    seed: int = 42
    load_events: int = 10_000
    load_private_scopes: int = 50
    load_group_scopes: int = 10
    load_owners: int = 1  # >1 enables true multi-tenant (distinct selfIds)

    @property
    def fingerprint(self) -> str:
        payload = json.dumps(
            {
                "tier": self.tier.value,
                "seed": self.seed,
                "load_events": self.load_events,
                "load_private_scopes": self.load_private_scopes,
                "load_group_scopes": self.load_group_scopes,
                "load_owners": self.load_owners,
            },
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()[:16]


@dataclass
class GeneratedDataset:
    config: TierConfig
    events: list[dict] = field(default_factory=list)
    scenes: list[str] = field(default_factory=list)

    def write(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            json.dumps(event, ensure_ascii=False, sort_keys=True)
            for event in self.events
        ]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def manifest(self, path: Path) -> dict:
        event_count = len(self.events)
        sources = [event.get("eventId", "") for event in self.events]
        digest = hashlib.sha256("\n".join(sorted(sources)).encode("utf-8")).hexdigest()[
            :16
        ]
        return {
            "dataset": "doppel-shadow-synthetic",
            "version": 1,
            "tier": self.config.tier.value,
            "seed": self.config.seed,
            "language": "zh-CN",
            "event_count": event_count,
            "tenant_count": len({str(e.get("selfId") or "") for e in self.events}),
            "scope_count": len(
                {
                    f"{e.get('selfId')}:{e.get('chatType')}:{e.get('chatId')}"
                    for e in self.events
                }
            ),
            "conversation_id_count": len(
                {f"{e.get('chatType')}:{e.get('chatId')}" for e in self.events}
            ),
            "actor_counts": _count_actors(self.events),
            "event_digest": digest,
            "generated_at": datetime.now(UTC).isoformat(),
            "notes": "synthetic events, never touches a real QQ link",
        }


def _count_actors(events: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for event in events:
        key = str(event.get("actorType") or "NONE")
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _generate_adapter(config: TierConfig) -> GeneratedDataset:
    events: list[dict] = []
    for scene in build_all_scenes():
        events.extend(event.payload for event in scene.events)
    return GeneratedDataset(config=config, events=events)


def _inject_noise(rng: random.Random, scene: Scene) -> list[dict]:
    """Add neutral chit-chat payloads around scene events without touching facts."""
    events = [event.payload for event in scene.events]
    insertions: list[tuple[int, dict]] = []
    for index in range(1, len(events)):
        if rng.random() < 0.35:
            noise = rng.choice(_NOISE_PHRASES + _CHITCHAT)
            noise_event = {
                "eventId": f"syn:noise:{scene.case_id}:{index}",
                "platform": "qq",
                "scene": events[0].get("scene") or "private",
                "eventType": "message",
                "chatType": events[0].get("chatType") or "private",
                "chatId": events[0].get("chatId") or "contact-001",
                "selfId": SELF_ID,
                "sender": {"id": SELF_ID, "name": "号主", "role": "self"},
                "text": noise,
                "attachments": [],
                "mentions": [],
                "timestamp": events[index - 1]["timestamp"],
                "rawPayload": {},
                "actorType": "OWNER",
                "platformMessageId": f"platform-noise-{scene.case_id}-{index}",
                "clientMessageId": f"client-noise-{scene.case_id}-{index}",
                "correlationId": None,
                "sequence": 9000 + index,
                "sentAt": events[index - 1]["timestamp"],
                "receivedAt": events[index - 1]["timestamp"],
                "importedAt": events[index - 1]["timestamp"],
                "direction": "out",
                "delegatedTaskId": None,
            }
            insertions.append((index, noise_event))
    for index, payload in reversed(insertions):
        events.insert(index, payload)
    return events


def _generate_quality(config: TierConfig) -> GeneratedDataset:
    rng = random.Random(config.seed)
    events: list[dict] = []
    scenes: list[str] = []
    for scene in build_all_scenes():
        events.extend(_inject_noise(rng, scene))
        scenes.append(scene.case_id)
    # extra chit-chat-only scenes to raise the noise floor
    for index in range(3):
        rng_local = random.Random(config.seed + index + 1)
        chat = _random_chit_chat_scene(rng_local, f"chit-chat-{index + 1}")
        events.extend(chat)
        scenes.append(f"chit-chat-{index + 1}")
    return GeneratedDataset(config=config, events=events, scenes=scenes)


def _random_chit_chat_scene(rng: random.Random, case_id: str) -> list[dict]:
    clock = SceneClock(datetime(2026, 5, 2, 10, 0, tzinfo=UTC))
    factory = EventFactory(self_id=SELF_ID, clock=clock, case_id=case_id)
    events: list[dict] = []
    chat_id = rng.choice(_CONTACT_POOL)[0]
    for step in range(rng.randint(4, 9)):
        if rng.random() < 0.6:
            sender_id, sender_name = _CONTACT_POOL[rng.randrange(len(_CONTACT_POOL))]
            msg = factory.contact(
                rng.choice(_CHITCHAT),
                chat_id=chat_id,
                offset=step * 3,
                sender_id=sender_id,
                sender_name=sender_name,
            )
        else:
            msg = factory.owner(
                rng.choice(_NOISE_PHRASES), chat_id=chat_id, offset=step * 3
            )
        events.append(msg.payload)
    return events


def _generate_load(config: TierConfig) -> GeneratedDataset:
    rng = random.Random(config.seed)
    clock_start = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    owner_count = max(int(config.load_owners), 1)
    owner_ids = [str(10000 + index) for index in range(1, owner_count + 1)]
    contact_pool = _CONTACT_POOL
    event_count = config.load_events
    events: list[dict] = []
    scope_ids: list[tuple[str, str]] = []
    for index in range(config.load_private_scopes):
        scope_ids.append(("private", f"contact-{1000 + index}"))
    for index in range(config.load_group_scopes):
        # unique group ids (not a reused pool), so the configured count is real
        scope_ids.append(("group", f"group-{2000 + index}"))
    if not scope_ids:
        scope_ids = [("private", "contact-1000")]
    exact_scopes = [
        (owner_id, chat_type, chat_id)
        for owner_id in owner_ids
        for chat_type, chat_id in scope_ids
    ]

    for seq in range(1, event_count + 1):
        owner_id, chat_type, chat_id = exact_scopes[(seq - 1) % len(exact_scopes)]
        now = clock_start + timedelta(minutes=seq * 7 % (60 * 24 * 90))
        is_owner = rng.random() < 0.30
        if is_owner:
            sender_id, sender_name = owner_id, f"号主{owner_id[-2:]}"
            role, actor_type, direction = "self", "OWNER", "out"
            text = rng.choice(_OWNER_FACTS)[0].format(
                city=rng.choice(["上海", "杭州", "苏州", "深圳"]),
                skill=rng.choice(["Python", "日语", "摄影", "吉他"]),
                pet=rng.choice(["猫", "狗", "仓鼠"]),
                pet_name=rng.choice(["年糕", "毛球", "豆豆"]),
                food=rng.choice(["火锅", "烧烤", "川菜"]),
                food_bad=rng.choice(["香菜", "折耳根", "苦瓜"]),
                company=rng.choice(["字节", "阿里", "外企"]),
            )
        else:
            sender_id, sender_name = contact_pool[seq % len(contact_pool)]
            role, actor_type, direction = ("peer", "CONTACT", "in")
            if rng.random() < 0.25:
                text = rng.choice(_CHITCHAT)
            else:
                text = rng.choice(_NOISE_PHRASES)
        event = {
            "eventId": f"syn:load:{seq}",
            "platform": "qq",
            "scene": "group" if chat_type == "group" else "private",
            "eventType": "message",
            "chatType": chat_type,
            "chatId": chat_id,
            "selfId": owner_id,
            "sender": {"id": sender_id, "name": sender_name, "role": role},
            "text": text,
            "attachments": [],
            "mentions": [],
            "timestamp": now.astimezone(UTC).isoformat().replace("+00:00", "+00:00"),
            "rawPayload": {},
            "actorType": actor_type,
            "platformMessageId": f"platform-load-{seq}",
            "clientMessageId": f"client-load-{seq}",
            "correlationId": None,
            "sequence": seq,
            "sentAt": now.astimezone(UTC).isoformat().replace("+00:00", "+00:00"),
            "receivedAt": now.astimezone(UTC).isoformat().replace("+00:00", "+00:00"),
            "importedAt": now.astimezone(UTC).isoformat().replace("+00:00", "+00:00"),
            "direction": direction,
            "delegatedTaskId": None,
        }
        events.append(event)
    return GeneratedDataset(config=config, events=events)


def generate_dataset(config: TierConfig) -> GeneratedDataset:
    if config.tier == Tier.ADAPTER:
        return _generate_adapter(config)
    if config.tier == Tier.QUALITY:
        return _generate_quality(config)
    if config.tier == Tier.LOAD:
        return _generate_load(config)
    raise ValueError(f"unknown tier: {config.tier}")
