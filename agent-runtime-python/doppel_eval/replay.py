"""Offline replay evaluation for Doppel shadow.

Reads synthetic datasets (adapter/quality/load), feeds every event through
the SAME bridge as live shadow, then runs a deterministic Doppel pipeline:

- ingest (idempotent by stable message id),
- optional second pass to verify replay idempotence,
- event-level recall against scene expectations,
- scope leakage / forbidden-evidence checks,
- an audit JSON report.

LLM extractor/consolidation is deliberately NOT part of this tier; it is
wired in via the reference pipeline (Phase 2b) when a provider is present.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.integrations.doppel.bridge import bridge_payload
from app.schemas.events import UnifiedEvent
from doppel_eval.events import SyntheticEvent
from doppel_eval.scenarios import Scene, SceneExpectation, build_all_scenes

logger = logging.getLogger(__name__)


def _status_value(result: Any) -> str:
    """WriteResult.status may be a StrEnum (value) or old-style str-Enum."""
    status = getattr(result, "status", "")
    if hasattr(status, "value"):
        return str(status.value)
    return str(status)


def _subject_actor(subject: str) -> str:
    if subject == "owner":
        return "owner"
    if subject == "agent":
        return "agent"
    return "contact"


async def _put_gold_memories(
    dm: Any, client: Any, scene: Scene, scopes: list[Any], logical_ids: dict[str, str]
) -> int:
    """Write scene gold memories (deterministic, no LLM) and return count."""
    put_count = 0
    for expectation in scene.expectations:
        if not expectation.memory_expected:
            continue
        scope = scopes[0]
        evidence_ids = expectation.expected_evidence or expectation.source_message_ids
        source_ids = [logical_ids[i] for i in evidence_ids if i in logical_ids]
        subject_id = ""
        if expectation.subject == "owner":
            subject_id = scope.user_id
        record = dm.MemoryRecord(
            kind=dm.MemoryKind.FACT,
            scope=scope,
            content=expectation.claim or expectation.claim_contains or "个人记忆",
            actor=_subject_actor(expectation.subject),
            authority=(
                dm.FactAuthority.HUMAN_SELF
                if expectation.subject == "owner"
                else dm.FactAuthority.PEER_STATEMENT
            ),
            state=dm.MemoryState.CONFIRMED,
            importance=0.7,
            tags=["personal-memory"],
            source_message_id=source_ids[0] if source_ids else "",
            source_event_id=source_ids[0] if source_ids else "",
            extractor="doppel.replay-gold.v1",
            metadata={
                "personal_memory_type": "fact",
                "subject": expectation.subject,
                "subject_id": subject_id,
                "temporal_status": expectation.temporal_status or "current",
                "topic_key": scene.case_id,
                "evidence_ids": source_ids,
            },
        )
        result = await client.put(record)
        status = _status_value(result)
        if status in {"created", "updated", "duplicate"}:
            put_count += 1
    return put_count


@dataclass
class ReplayQueryResult:
    name: str
    text: str
    query_type: str = "natural"  # natural | lexical
    expected_memory: bool = True
    expected_hit: bool = False
    forbidden_hit: bool = False
    subject_ok: bool = True
    temporal_ok: bool = True
    evidence_ok: bool = True
    evidence_spans_multiple_hits: bool = False
    forbidden_evidence_ok: bool = True
    ambiguous_ok: bool = True
    count_status: str = "not_requested"
    count_value: int | None = None
    distinct_event_keys: list[str] = field(default_factory=list)
    count_ok: bool = True
    complete: bool = True
    warnings: list[str] = field(default_factory=list)
    leakage: bool = False
    recalled_ids: list[str] = field(default_factory=list)
    latency_ms: float = 0.0
    error: str = ""

    @property
    def hard_failure(self) -> bool:
        """Failures that must gate the run: crashes, leaks, forbidden content."""
        return bool(
            self.error
            or self.leakage
            or self.forbidden_hit
            or not self.forbidden_evidence_ok
        )


@dataclass
class ReplaySceneResult:
    case_id: str
    category: str
    description: str
    ingested: int = 0
    duplicate_events: int = 0
    gold_put: int = 0
    queries: list[ReplayQueryResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        if not self.queries:
            return False
        for query in self.queries:
            if query.hard_failure:
                return False
            if query.expected_memory and not query.expected_hit:
                return False
            if not query.expected_memory and query.expected_hit:
                return False
            if not query.subject_ok or not query.temporal_ok or not query.evidence_ok:
                return False
            if not query.ambiguous_ok:
                return False
            if not query.count_ok:
                return False
        return True


def _expectation_passed(
    expectation: SceneExpectation, query_result: ReplayQueryResult
) -> bool:
    """A scene query passes when it recalls the intended memory and no forbidden claim."""
    if query_result.error:
        return False
    if query_result.forbidden_hit:
        return False
    if expectation.memory_expected:
        return query_result.expected_hit
    # memory_expected=False: the query must NOT surface that claim.
    return not query_result.expected_hit


def _load_doppel() -> Any | None:
    """Import doppel_memory from DOPPEL_IMPORT_PATH or the running env."""
    path = os.environ.get("DOPPEL_IMPORT_PATH", "")
    if path:
        path_obj = Path(path)
        if path_obj.is_dir() and str(path_obj) not in sys.path:
            sys.path.insert(0, str(path_obj))
    try:
        import doppel_memory  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        logger.warning("doppel_memory not importable; replay requires it: %s", exc)
        return None
    return sys.modules["doppel_memory"]


def _scope_object(dm: Any, scope: dict) -> Any:
    return dm.MemoryScope(**scope)


def _message_object(dm: Any, message: dict) -> Any:
    return dm.ChatMessage(**message)


def _unified(event: SyntheticEvent) -> UnifiedEvent:
    """SyntheticEvent payload -> runtime UnifiedEvent (single schema)."""
    return UnifiedEvent.model_validate(event.payload)


def _scene_scopes(dm: Any, scene: Scene) -> tuple[list[Any], dict]:
    """Distinct exact scopes plus the user-level scope for one scene."""
    scopes: dict[str, Any] = {}
    user_scope = None
    for event in scene.events:
        payload = bridge_payload(_unified(event))
        scope = _scope_object(dm, payload["scope"])
        scopes[scope.scope_key] = scope
        if user_scope is None:
            user_scope = scope.user_scope()
    return list(scopes.values()), user_scope


def _logical_to_message_id(scene: Scene) -> dict[str, str]:
    """Map logical ids (case:mN) to the bridge's stable message ids."""
    mapping: dict[str, str] = {}
    for event in scene.events:
        payload = bridge_payload(_unified(event))
        short = f"m{event.seq}"
        mapping[short] = payload["message_id"]
        mapping[f"{event.case_id}:{short}"] = payload["message_id"]
    return mapping


def _hit_content(hit: Any) -> str:
    record = getattr(hit, "record", None)
    if record is not None:
        return str(getattr(record, "content", ""))
    return str(getattr(hit, "content", ""))


def _hit_id(hit: Any) -> str:
    record = getattr(hit, "record", None)
    if record is not None:
        return str(getattr(record, "memory_id", ""))
    return str(getattr(hit, "memory_id", ""))


def _hit_record(hit: Any) -> Any:
    return getattr(hit, "record", None) or hit


def _record_metadata(record: Any, key: str) -> Any:
    metadata = getattr(record, "metadata", None) or {}
    if isinstance(metadata, dict):
        return metadata.get(key, "")
    return ""


def _record_evidence_ids(record: Any) -> set[str]:
    raw = _record_metadata(record, "evidence_ids")
    if not raw:
        raw = _record_metadata(record, "evidence")
    if not raw:
        return set()
    try:
        parsed = (
            json.loads(raw) if isinstance(raw, str) and raw.startswith("[") else raw
        )
    except ValueError:
        return set()
    if isinstance(parsed, str):
        return {item.strip() for item in parsed.split(",") if item.strip()}
    if isinstance(parsed, list):
        evidence: set[str] = set()
        for item in parsed:
            if isinstance(item, dict):
                value = str(item.get("evidence_id", "") or "").strip()
            else:
                value = str(item or "").strip()
            if value:
                evidence.add(value)
        return evidence
    return set()


def _record_scope_key(record: Any) -> str:
    scope = getattr(record, "scope", None)
    if scope is not None:
        key = getattr(scope, "scope_key", "")
        if key:
            return str(key)
    return ""


async def _run_one_query(
    dm: Any,
    client: Any,
    scene: Scene,
    expectation: SceneExpectation,
    query_scopes: list[Any],
    query_text: str,
    query_type: str,
    logical_ids: dict[str, str],
    *,
    check_ambiguous: bool = True,
    semantic_index: Any | None = None,
) -> ReplayQueryResult:
    started = time.perf_counter()
    query_result = ReplayQueryResult(
        name=query_text[:20],
        text=query_text,
        query_type=query_type,
        expected_memory=expectation.memory_expected,
    )
    try:
        query_now = scene.base_time + timedelta(
            minutes=expectation.query_now_offset_minutes
        )
        result = await client.query_personal_memory(
            query_text,
            query_scopes,
            now=query_now,
            semantic_index=semantic_index,
        )
        hits = getattr(result, "hits", []) or []
        query_result.complete = bool(getattr(result, "complete", True))
        query_result.warnings = list(getattr(result, "warnings", []) or [])
        hit_contents = [_hit_content(h) for h in hits]
        hit_records = [_hit_record(h) for h in hits]
        query_result.recalled_ids = [_hit_id(h) for h in hits]
        query_result.expected_hit = any(
            expectation.claim_contains and expectation.claim_contains in text
            for text in hit_contents
        )
        query_result.forbidden_hit = any(
            any(forbidden in text for forbidden in expectation.forbidden_claims)
            for text in hit_contents
        )
        # ---- structural assertions on the returned records ----
        allowed_scope_keys = {scope.scope_key for scope in query_scopes}
        expected_subject = (
            expectation.subject if expectation.subject not in {"", "none"} else ""
        )
        expected_temporal = expectation.temporal_status or ""
        expected_evidence = {
            logical_ids[i] for i in expectation.expected_evidence if i in logical_ids
        }
        forbidden_evidence = {
            logical_ids[i] for i in expectation.forbidden_evidence if i in logical_ids
        }
        subjects = [
            str(_record_metadata(record, "subject") or "").strip()
            for record in hit_records
        ]
        temporals = [
            str(_record_metadata(record, "temporal_status") or "").strip()
            for record in hit_records
        ]
        evidence_sets = [set(_record_evidence_ids(record)) for record in hit_records]
        query_result.subject_ok = not expectation.memory_expected or (
            not expected_subject
            or any(subject == expected_subject for subject in subjects)
        )
        query_result.temporal_ok = not expectation.memory_expected or (
            not expected_temporal
            or any(temporal == expected_temporal for temporal in temporals)
        )
        evidence_union = set().union(*evidence_sets) if evidence_sets else set()
        single_record_evidence_ok = any(
            expected_evidence <= evidence for evidence in evidence_sets
        )
        query_result.evidence_spans_multiple_hits = bool(
            expected_evidence
            and expected_evidence <= evidence_union
            and not single_record_evidence_ok
        )
        query_result.evidence_ok = not expectation.memory_expected or (
            not expected_evidence
            or single_record_evidence_ok
            or (
                expectation.evidence_may_span_hits
                and expected_evidence <= evidence_union
            )
        )
        query_result.forbidden_evidence_ok = not any(
            forbidden_evidence & evidence for evidence in evidence_sets
        )
        # scope leakage: every returned record must belong to an allowed scope
        leaked = [
            _record_scope_key(record)
            for record in hit_records
            if _record_scope_key(record) not in allowed_scope_keys
        ]
        query_result.leakage = bool(leaked)
        if expectation.ambiguous is not None and check_ambiguous:
            query_result.ambiguous_ok = (
                getattr(result, "ambiguous", False) == expectation.ambiguous
            )
        count = getattr(result, "count", None)
        if count is not None:
            query_result.count_status = str(getattr(count, "status", ""))
            query_result.count_value = getattr(count, "value", None)
            query_result.distinct_event_keys = list(
                getattr(count, "distinct_event_keys", []) or []
            )
        if expectation.expected_count_status:
            query_result.count_ok = (
                query_result.count_status == expectation.expected_count_status
            )
        if expectation.expected_count is not None:
            query_result.count_ok = (
                query_result.count_ok
                and query_result.count_value == expectation.expected_count
            )
        if expectation.expected_distinct_event_keys is not None:
            query_result.count_ok = (
                query_result.count_ok
                and len(query_result.distinct_event_keys)
                == expectation.expected_distinct_event_keys
            )
    except Exception as exc:  # noqa: BLE001 - audit boundary
        query_result.error = f"{type(exc).__name__}: {exc}"
    query_result.latency_ms = round((time.perf_counter() - started) * 1000, 3)
    return query_result


async def replay_scenarios(
    dm: Any,
    *,
    replay_twice: bool = False,
    query_now: datetime | None = None,
    check_ambiguous: bool = False,
) -> dict[str, Any]:
    """Run every built-in scene through ingest + recall; return audit payload.

    ``check_ambiguous=False`` (default, contract mode): conflict ambiguity
    assertions are skipped because gold injection cannot create conflict
    markers.  End-to-end mode passes True to require real conflict evidence.
    """
    results: list[ReplaySceneResult] = []
    now = query_now or datetime(2026, 6, 1, 0, 0, tzinfo=UTC)
    for scene in build_all_scenes():
        scene_result = ReplaySceneResult(
            case_id=scene.case_id,
            category=scene.category,
            description=scene.description,
        )
        db_path = Path(tempfile.mkdtemp(prefix="doppel-replay-")) / "replay.sqlite3"
        client = dm.DoppelClient(backend="sqlite", database=str(db_path))
        try:
            scopes, user_scope = _scene_scopes(dm, scene)
            logical_ids = _logical_to_message_id(scene)
            ingested = 0
            for event in scene.events:
                unified = _unified(event)
                payload = bridge_payload(unified)
                scope = _scope_object(dm, payload["scope"])
                message = _message_object(dm, payload["message"])
                result = await client.ingest(scope, message)
                status = _status_value(result)
                if status in {"created", "updated", "duplicate"}:
                    ingested += 1
            scene_result.ingested = ingested
            if replay_twice:
                duplicate_events = 0
                for event in scene.events:
                    unified = _unified(event)
                    payload = bridge_payload(unified)
                    scope = _scope_object(dm, payload["scope"])
                    message = _message_object(dm, payload["message"])
                    result = await client.ingest(scope, message)
                    if _status_value(result) == "duplicate":
                        duplicate_events += 1
                scene_result.duplicate_events = duplicate_events

            query_scopes = scopes
            if user_scope is not None:
                query_scopes = [*scopes, user_scope]
            gold_put = await _put_gold_memories(
                dm, client, scene, query_scopes, logical_ids
            )
            scene_result.gold_put = gold_put
            for expectation in scene.expectations:
                if not expectation.query:
                    continue
                scene_result.queries.append(
                    await _run_one_query(
                        dm,
                        client,
                        scene,
                        expectation,
                        query_scopes,
                        expectation.query,
                        "natural",
                        logical_ids,
                        check_ambiguous=check_ambiguous,
                    )
                )
                if expectation.query_lexical:
                    scene_result.queries.append(
                        await _run_one_query(
                            dm,
                            client,
                            scene,
                            expectation,
                            query_scopes,
                            expectation.query_lexical,
                            "lexical",
                            logical_ids,
                            check_ambiguous=check_ambiguous,
                        )
                    )
        except Exception as exc:  # noqa: BLE001
            scene_result.queries.append(
                ReplayQueryResult(
                    name="scene", text="", error=f"{type(exc).__name__}: {exc}"
                )
            )
        finally:
            await client.close()
        results.append(scene_result)

    hard_failures = sum(1 for r in results for q in r.queries if q.hard_failure)
    leakage = sum(1 for r in results for q in r.queries if q.leakage)
    summary = {
        "mode": "contract",
        "label": "personal-query-contract benchmark (gold records injected; does NOT exercise extraction/consolidation)",
        "scenario_count": len(results),
        "query_count": sum(len(r.queries) for r in results),
        "passed_scenarios": sum(1 for r in results if r.passed and r.queries),
        "hard_failures": hard_failures,
        "forbidden_hits": sum(1 for r in results for q in r.queries if q.forbidden_hit),
        "leakage_count": leakage,
        "total_ingested": sum(r.ingested for r in results),
        "gold_memories_put": sum(r.gold_put for r in results),
        "duplicate_events_total": sum(r.duplicate_events for r in results),
    }
    gate = {
        "ok": hard_failures == 0 and leakage == 0,
        "strict_passed": all(r.passed for r in results),
        "hard_failures": hard_failures,
        "leakage": leakage,
        "passed_scenarios": summary["passed_scenarios"],
    }
    return {
        "runner": "doppel.replay.v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "config": {"replay_twice": replay_twice, "query_now": now.isoformat()},
        "summary": summary,
        "gate": gate,
        "scenarios": [
            {
                "case_id": r.case_id,
                "category": r.category,
                "description": r.description,
                "ingested": r.ingested,
                "duplicate_events": r.duplicate_events,
                "gold_put": r.gold_put,
                "passed": r.passed,
                "queries": [
                    {
                        "name": q.name,
                        "text": q.text,
                        "query_type": q.query_type,
                        "expected_memory": q.expected_memory,
                        "expected_hit": q.expected_hit,
                        "forbidden_hit": q.forbidden_hit,
                        "subject_ok": q.subject_ok,
                        "temporal_ok": q.temporal_ok,
                        "evidence_ok": q.evidence_ok,
                        "evidence_spans_multiple_hits": q.evidence_spans_multiple_hits,
                        "forbidden_evidence_ok": q.forbidden_evidence_ok,
                        "ambiguous_ok": q.ambiguous_ok,
                        "count_status": q.count_status,
                        "count_value": q.count_value,
                        "distinct_event_keys": q.distinct_event_keys,
                        "count_ok": q.count_ok,
                        "complete": q.complete,
                        "warnings": q.warnings,
                        "leakage": q.leakage,
                        "recalled_ids": q.recalled_ids,
                        "latency_ms": q.latency_ms,
                        "error": q.error,
                    }
                    for q in r.queries
                ],
            }
            for r in results
        ],
    }


async def replay_dataset(
    dm: Any,
    dataset_path: Path,
    *,
    replay_twice: bool = False,
) -> dict[str, Any]:
    """Replay a raw JSONL dataset (no scene expectations): ingest + idempotence."""
    events: list[dict] = []
    with dataset_path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    db_path = Path(tempfile.mkdtemp(prefix="doppel-replay-")) / "replay.sqlite3"
    client = dm.DoppelClient(backend="sqlite", database=str(db_path))
    status_counts: dict[str, int] = {}
    failures: list[dict[str, str]] = []
    started = time.perf_counter()

    async def _ingest(raw: dict) -> str:
        try:
            event = UnifiedEvent.model_validate(raw)
            payload = bridge_payload(event)
            if payload["errors"] or payload["scope"] is None:
                raise ValueError("; ".join(payload["errors"]) or "missing scope")
            scope = _scope_object(dm, payload["scope"])
            message = _message_object(dm, payload["message"])
            result = await client.ingest(scope, message)
            return _status_value(result) or "unknown"
        except Exception as exc:  # noqa: BLE001 - replay audit boundary
            if len(failures) < 20:
                failures.append(
                    {
                        "event_id": str(raw.get("eventId") or ""),
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
            return "failed"

    def _count(status: str) -> None:
        status_counts[status] = status_counts.get(status, 0) + 1

    try:
        for raw in events:
            _count(await _ingest(raw))
        if replay_twice:
            for raw in events:
                _count(await _ingest(raw))
    finally:
        await client.close()
    elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
    ingested = status_counts.get("created", 0) + status_counts.get("updated", 0)
    duplicates = status_counts.get("duplicate", 0)
    failed = status_counts.get("failed", 0) + status_counts.get("unknown", 0)
    return {
        "runner": "doppel.replay-dataset.v1",
        "dataset": dataset_path.name,
        "generated_at": datetime.now(UTC).isoformat(),
        "event_count": len(events),
        "ingested": ingested,
        "duplicates": duplicates,
        "status_counts": dict(sorted(status_counts.items())),
        "failures": failures,
        "elapsed_ms": elapsed_ms,
        "throughput_events_per_sec": round(len(events) / (elapsed_ms / 1000), 1)
        if elapsed_ms
        else 0.0,
        "gate": {"ok": failed == 0, "failed_writes": failed},
    }
