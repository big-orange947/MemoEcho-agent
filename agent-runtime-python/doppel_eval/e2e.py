"""Synthetic extraction -> consolidation -> query evaluation with an LLM."""

from __future__ import annotations

import tempfile
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.integrations.doppel.bridge import bridge_payload
from doppel_eval.provider import (
    BudgetedCachedStructuredOutputModel,
    ProviderBudget,
    ProviderUsageLedger,
    RetryEmptyJsonOnceModel,
)
from doppel_eval.replay import (
    ReplayQueryResult,
    _logical_to_message_id,
    _message_object,
    _run_one_query,
    _scope_object,
    _status_value,
    _unified,
)
from doppel_eval.scenarios import Scene, build_e2e_scenes


async def run_e2e(
    dm: Any,
    *,
    model: str,
    base_url: str,
    api_key: str = "",
    schema_mode: str = "json_schema",
    max_tokens_parameter: str = "max_completion_tokens",
    thinking: str | None = None,
    budget: ProviderBudget | None = None,
    cache_dir: Path | None = None,
    case_ids: list[str] | None = None,
    max_scenes: int = 10,
) -> dict[str, Any]:
    """Run bounded synthetic scenes through real extraction and deterministic merge."""
    if not model.strip():
        raise ValueError("an E2E provider model is required")
    bound_budget = budget or ProviderBudget()
    ledger = ProviderUsageLedger(bound_budget)
    provider = dm.OpenAICompatibleStructuredOutputModel(
        dm.OpenAICompatibleStructuredOutputConfig(
            model=model,
            base_url=base_url,
            schema_mode=schema_mode,
            max_completion_tokens=bound_budget.max_output_tokens_per_call,
            max_tokens_parameter=max_tokens_parameter,
            temperature=0,
            thinking=thinking,
        ),
        api_key=api_key,
        usage_observer=ledger.observe_usage,
    )
    cached = BudgetedCachedStructuredOutputModel(
        provider, ledger, cache_dir=cache_dir
    )
    analyzer = dm.ReferencePersonalMemoryAnalyzer(RetryEmptyJsonOnceModel(cached))
    miner = dm.PersonalMemoryMiner(
        analyzer,
        dm.PersonalMemoryMinerConfig(page_size=200, max_messages=500),
    )
    consolidator = dm.DeterministicMemoryConsolidator()
    selected = _select_scenes(case_ids, max_scenes)
    scenario_reports: list[dict[str, Any]] = []
    stopped = False
    try:
        for scene in selected:
            report = await _run_scene(dm, scene, miner, consolidator, ledger)
            scenario_reports.append(report)
            if ledger.stopped_reason:
                stopped = True
                break
    finally:
        await provider.aclose()

    passed = sum(1 for report in scenario_reports if report["passed"])
    query_hard_failures = sum(
        1
        for report in scenario_reports
        for query in report["queries"]
        if query["error"] or query["leakage"] or query["forbidden_hit"]
    )
    unexpected_processing_errors = sum(
        report["unexpected_processing_errors"] for report in scenario_reports
    )
    safety_rejections = sum(
        report["safety_rejections"] for report in scenario_reports
    )
    hard_failures = query_hard_failures + unexpected_processing_errors
    return {
        "runner": "doppel.e2e.v2",
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": "synthetic-llm-e2e",
        "label": (
            "real LLM PersonalMemoryMiner + deterministic consolidation + "
            "personal-memory query; no gold memories injected"
        ),
        "provider": {
            "model": model,
            "base_url": base_url,
            "schema_mode": schema_mode,
            "thinking": thinking,
            "api_key_persisted": False,
        },
        "usage": ledger.report(),
        "summary": {
            "requested_scenarios": len(selected),
            "completed_scenarios": len(scenario_reports),
            "passed_scenarios": passed,
            "hard_failures": hard_failures,
            "query_hard_failures": query_hard_failures,
            "unexpected_processing_errors": unexpected_processing_errors,
            "safety_rejections": safety_rejections,
            "provider_errors": ledger.provider_errors,
            "stopped_by_budget": stopped,
            "provider_calls": ledger.calls_attempted,
            "cache_hits": ledger.cache_hits,
            "total_tokens": ledger.total_tokens,
        },
        "gate": {
            "ok": hard_failures == 0 and ledger.provider_errors == 0 and not stopped,
            "strict_passed": (
                len(scenario_reports) == len(selected)
                and all(report["passed"] for report in scenario_reports)
                and not stopped
            ),
            "within_budget": not stopped,
        },
        "scenarios": scenario_reports,
    }


def _select_scenes(case_ids: list[str] | None, max_scenes: int) -> list[Scene]:
    if max_scenes < 1:
        raise ValueError("max_scenes must be positive")
    scenes = build_e2e_scenes()
    if case_ids:
        requested = set(case_ids)
        scenes = [scene for scene in scenes if scene.case_id in requested]
        missing = requested - {scene.case_id for scene in scenes}
        if missing:
            raise ValueError(f"unknown E2E case ids: {sorted(missing)}")
    return scenes[:max_scenes]


async def _run_scene(
    dm: Any,
    scene: Scene,
    miner: Any,
    consolidator: Any,
    ledger: ProviderUsageLedger,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="doppel-e2e-") as tmp:
        client = dm.DoppelClient(
            backend="sqlite", database=str(Path(tmp) / "e2e.sqlite3")
        )
        ingested = 0
        processing: list[dict[str, Any]] = []
        queries: list[ReplayQueryResult] = []
        memories: list[dict[str, Any]] = []
        try:
            scope_messages: dict[str, tuple[Any, list[Any]]] = {}
            for event in scene.events:
                payload = bridge_payload(_unified(event))
                scope = _scope_object(dm, payload["scope"])
                message = _message_object(dm, payload["message"])
                result = await client.ingest(scope, message)
                if _status_value(result) in {"created", "updated", "duplicate"}:
                    ingested += 1
                if scope.scope_key not in scope_messages:
                    scope_messages[scope.scope_key] = (scope, [])
                scope_messages[scope.scope_key][1].append(message)

            query_scopes: dict[str, Any] = {}
            for scope, messages in scope_messages.values():
                query_scopes[scope.scope_key] = scope
                user_scope = scope.user_scope()
                query_scopes[user_scope.scope_key] = user_scope
                start = min(message.at for message in messages) - timedelta(seconds=1)
                end = max(message.at for message in messages) + timedelta(seconds=1)
                result = await client.run_batch_task(
                    miner,
                    scope,
                    dm.HistoryWindow(start=start, end=end),
                    allowed_scopes=[scope, user_scope],
                    run_id=f"e2e:{scene.case_id}:{scope.scope_key[:12]}",
                )
                processing.append(
                    {
                        "scope": scope.describe(),
                        "history_messages": result.history_messages_read,
                        "proposals": len(result.proposals),
                        "writes": _write_status_counts(result.write_results),
                        "errors": [
                            _serialize_processing_error(error)
                            for error in result.errors
                        ],
                    }
                )
                if ledger.stopped_reason:
                    break

            if not ledger.stopped_reason:
                for scope in query_scopes.values():
                    result = await client.consolidate(
                        consolidator,
                        scope,
                        run_id=f"e2e-consolidate:{scene.case_id}:{scope.scope_key[:12]}",
                    )
                    if result.errors:
                        processing.append(
                            {
                                "scope": scope.describe(),
                                "stage": "consolidation",
                                "errors": [
                                    _serialize_processing_error(error)
                                    for error in result.errors
                                ],
                            }
                        )

                logical_ids = _logical_to_message_id(scene)
                for expectation in scene.expectations:
                    if not expectation.query:
                        continue
                    queries.append(
                        await _run_one_query(
                            dm,
                            client,
                            scene,
                            expectation,
                            list(query_scopes.values()),
                            expectation.query,
                            "natural",
                            logical_ids,
                            check_ambiguous=True,
                        )
                    )
                memories = await _memory_snapshots(dm, client, query_scopes.values())
        finally:
            await client.close()

    serialized_queries = [_serialize_query(query) for query in queries]
    processing_errors = [
        error
        for item in processing
        for error in item.get("errors", [])
        if isinstance(error, dict)
    ]
    safety_rejections = sum(
        1 for error in processing_errors if error.get("classification") == "safety"
    )
    unexpected_processing_errors = len(processing_errors) - safety_rejections
    passed = (
        bool(serialized_queries)
        and unexpected_processing_errors == 0
        and all(_query_passed(query) for query in serialized_queries)
    )
    return {
        "case_id": scene.case_id,
        "category": scene.category,
        "description": scene.description,
        "ingested": ingested,
        "processing": processing,
        "safety_rejections": safety_rejections,
        "unexpected_processing_errors": unexpected_processing_errors,
        "memories": memories,
        "passed": passed,
        "queries": serialized_queries,
    }


def _serialize_processing_error(error: Any) -> dict[str, str]:
    error_type = str(getattr(error, "error_type", type(error).__name__) or "")
    return {
        "stage": str(getattr(error, "stage", "") or ""),
        "error_type": error_type,
        "message": str(getattr(error, "message", error)),
        "processor": str(getattr(error, "processor", "") or ""),
        "classification": (
            "safety"
            if error_type == "PersonalMemoryEvidenceError"
            else "unexpected"
        ),
    }


def _write_status_counts(results: list[Any]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for result in results:
        counts[_status_value(result) or "unknown"] += 1
    return dict(sorted(counts.items()))


async def _memory_snapshots(
    dm: Any, client: Any, scopes: Any
) -> list[dict[str, Any]]:
    snapshots: list[dict[str, Any]] = []
    for scope in scopes:
        cursor = ""
        while True:
            page = await client.store.scan(
                scope,
                filters=dm.MemoryFilter(tags={"personal-memory"}),
                cursor=cursor,
                limit=200,
            )
            for record in page.records:
                metadata = record.metadata or {}
                evidence = metadata.get("evidence", [])
                evidence_ids = [
                    str(item.get("evidence_id", "") or "")
                    for item in evidence
                    if isinstance(item, dict) and item.get("evidence_id")
                ]
                snapshots.append(
                    {
                        "memory_id": record.memory_id,
                        "scope": scope.describe(),
                        "content": record.content,
                        "state": getattr(record.state, "value", str(record.state)),
                        "memory_type": metadata.get("personal_memory_type", ""),
                        "topic_key": metadata.get("topic_key", ""),
                        "temporal_status": metadata.get("temporal_status", ""),
                        "event_key": metadata.get("event_key", ""),
                        "revision_kind": metadata.get("revision_kind", ""),
                        "consolidation_operation": (
                            metadata.get("consolidation", {}).get("operation", "")
                            if isinstance(metadata.get("consolidation"), dict)
                            else ""
                        ),
                        "evidence_ids": evidence_ids,
                    }
                )
            if not page.has_more:
                break
            cursor = page.next_cursor
    snapshots.sort(key=lambda item: (str(item["scope"]), item["memory_id"]))
    return snapshots


def _serialize_query(query: ReplayQueryResult) -> dict[str, Any]:
    return {
        "name": query.name,
        "text": query.text,
        "expected_memory": query.expected_memory,
        "expected_hit": query.expected_hit,
        "forbidden_hit": query.forbidden_hit,
        "subject_ok": query.subject_ok,
        "temporal_ok": query.temporal_ok,
        "evidence_ok": query.evidence_ok,
        "evidence_spans_multiple_hits": query.evidence_spans_multiple_hits,
        "forbidden_evidence_ok": query.forbidden_evidence_ok,
        "ambiguous_ok": query.ambiguous_ok,
        "count_status": query.count_status,
        "count_value": query.count_value,
        "distinct_event_keys": query.distinct_event_keys,
        "count_ok": query.count_ok,
        "leakage": query.leakage,
        "recalled_ids": query.recalled_ids,
        "latency_ms": query.latency_ms,
        "error": query.error,
    }


def _query_passed(query: dict[str, Any]) -> bool:
    if query["error"] or query["leakage"] or query["forbidden_hit"]:
        return False
    if query["expected_memory"] != query["expected_hit"]:
        return False
    return bool(
        query["subject_ok"]
        and query["temporal_ok"]
        and query["evidence_ok"]
        and query["forbidden_evidence_ok"]
        and query["ambiguous_ok"]
        and query["count_ok"]
    )
