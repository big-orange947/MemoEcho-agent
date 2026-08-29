"""Synthetic event generation tests: schema conformance + determinism."""

from __future__ import annotations

import json
from pathlib import Path

from doppel_eval.events import EventFactory, SceneClock
from doppel_eval.generators import Tier, TierConfig, generate_dataset
from doppel_eval.scenarios import build_all_scenes, build_e2e_scenes, build_scene


def _validate(event: dict) -> None:
    """Validate against the runtime's UnifiedEvent schema when importable."""
    try:
        from app.schemas.events import UnifiedEvent
    except ImportError:  # pragma: no cover - runtime not importable
        return
    unified = UnifiedEvent.model_validate(event)
    assert unified.event_id == event["eventId"]


def test_all_scenes_are_registered_and_buildable() -> None:
    scenes = build_all_scenes()
    assert len(scenes) >= 10
    for scene in scenes:
        assert scene.case_id and scene.category
        assert len(scene.events) >= 2, f"{scene.case_id} has too few events"


def test_e2e_adds_three_travel_count_scenes_without_changing_contract_set() -> None:
    contract_ids = {scene.case_id for scene in build_all_scenes()}
    e2e_ids = {scene.case_id for scene in build_e2e_scenes()}

    assert len(contract_ids) == 11
    assert e2e_ids - contract_ids == {
        "travel-count-two-distinct",
        "travel-count-repeat-same",
        "travel-count-cancelled-plan",
    }


def test_scene_events_validate_against_unified_event_schema() -> None:
    for scene in build_all_scenes():
        for event in scene.events:
            _validate(event.payload)


def test_actor_semantics_match_runtime_rules() -> None:
    scene = build_scene("agent-output")
    agent_event = next(
        event.payload
        for event in scene.events
        if event.payload.get("actorType") == "AGENT"
    )
    assert agent_event["actorType"] == "AGENT"
    assert agent_event["sender"]["role"] == "self"
    assert agent_event["direction"] == "out"
    # The core semantic: an agent-output scene contains NO owner-typed event,
    # even though it is sent from the account's own QQ id.
    owner_events = [
        event for event in scene.events if event.payload.get("actorType") == "OWNER"
    ]
    assert owner_events == []
    contact_event = next(
        event.payload
        for event in scene.events
        if event.payload.get("actorType") == "CONTACT"
    )
    assert contact_event["sender"]["role"] == "peer"
    assert contact_event["direction"] == "in"
    # Owner semantics verified on a scene that includes manual owner sends.
    owner_scene = build_scene("stable-preference-repeat")
    owner_event = next(
        event.payload
        for event in owner_scene.events
        if event.payload.get("actorType") == "OWNER"
    )
    assert owner_event["sender"]["role"] == "self"
    assert owner_event["direction"] == "out"


def test_message_identity_is_stable_and_typed() -> None:
    scene = build_scene("stable-preference-repeat")
    payload = scene.events[1].payload
    assert payload["clientMessageId"].startswith("client-")
    assert payload["platformMessageId"].startswith("platform-")
    # identity priority: client > platform (runtime rule)
    assert payload["clientMessageId"] != payload["platformMessageId"]


def test_adapter_tier_generation_is_deterministic() -> None:
    first = generate_dataset(TierConfig(tier=Tier.ADAPTER, seed=7))
    second = generate_dataset(TierConfig(tier=Tier.ADAPTER, seed=7))
    assert [event["eventId"] for event in first.events] == [
        event["eventId"] for event in second.events
    ]
    assert len(first.events) >= 40


def test_quality_tier_has_noise_and_deterministic() -> None:
    first = generate_dataset(TierConfig(tier=Tier.QUALITY, seed=9))
    second = generate_dataset(TierConfig(tier=Tier.QUALITY, seed=9))
    assert [event["eventId"] for event in first.events] == [
        event["eventId"] for event in second.events
    ]
    noise_ids = [
        event["eventId"] for event in first.events if "noise" in event["eventId"]
    ]
    assert len(noise_ids) >= 5
    for event in first.events:
        _validate(event)


def test_load_tier_scales_and_isolates_scopes() -> None:
    dataset = generate_dataset(
        TierConfig(
            tier=Tier.LOAD, load_events=200, load_private_scopes=5, load_group_scopes=2
        )
    )
    assert len(dataset.events) == 200
    scopes = {(event["chatType"], event["chatId"]) for event in dataset.events}
    assert len(scopes) == 7
    for event in dataset.events:
        _validate(event)
        assert event["direction"] in {"in", "out"}
        assert event["actorType"] in {"OWNER", "CONTACT"}


def test_load_tier_reuses_each_chat_across_tenants() -> None:
    dataset = generate_dataset(
        TierConfig(
            tier=Tier.LOAD,
            load_events=30,
            load_private_scopes=3,
            load_group_scopes=0,
            load_owners=2,
        )
    )
    owners_by_chat: dict[str, set[str]] = {}
    for event in dataset.events:
        owners_by_chat.setdefault(event["chatId"], set()).add(event["selfId"])
    assert len(owners_by_chat) == 3
    assert all(len(owners) == 2 for owners in owners_by_chat.values())
    manifest = dataset.manifest(Path("load.jsonl"))
    assert manifest["tenant_count"] == 2
    assert manifest["conversation_id_count"] == 3
    assert manifest["scope_count"] == 6


def test_generated_dataset_write_and_manifest(tmp_path) -> None:
    dataset = generate_dataset(TierConfig(tier=Tier.ADAPTER, seed=3))
    path = tmp_path / "adapter.jsonl"
    dataset.write(path)
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == len(dataset.events)
    manifest = dataset.manifest(path)
    assert manifest["event_count"] == len(dataset.events)
    assert manifest["event_digest"]
    json.dumps(manifest)


def test_event_builder_produces_unique_ids() -> None:
    clock = SceneClock(
        __import__("datetime").datetime(2026, 1, 1, tzinfo=__import__("datetime").UTC)
    )
    factory = EventFactory(self_id="10001", clock=clock, case_id="uniq")
    events = [
        factory.owner("第一条", offset=0),
        factory.contact("回复", offset=1),
        factory.owner("第二条", offset=2),
    ]
    ids = [event.payload["eventId"] for event in events]
    assert len(set(ids)) == len(ids)
    assert ids[0].startswith("syn:uniq:event:1")
