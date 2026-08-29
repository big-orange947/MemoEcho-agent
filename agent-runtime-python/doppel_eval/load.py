"""Synthetic load evaluation: large offline event streams, never QQ.

Streams a generated JSONL dataset into Doppel, measures throughput,
verifies replay idempotence and scope isolation.  Deterministic pipeline
only (no LLM), suitable for SQLite or PostgreSQL backends.
"""

from __future__ import annotations

import json
import logging
import tempfile
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

from app.integrations.doppel.bridge import bridge_payload
from app.schemas.events import UnifiedEvent

logger = logging.getLogger(__name__)


def _iter_events(dataset_path: Path) -> Iterator[dict]:
    with dataset_path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                yield json.loads(line)


def _status_value(result: Any) -> str:
    status = getattr(result, "status", "")
    if hasattr(status, "value"):
        return str(status.value)
    return str(status)


async def _isolation_check(
    dm: Any, client: Any, scopes: list[Any], *, check_limit: int = 10
) -> dict:
    """Each sampled scope must only return records from that exact scope."""
    checked = 0
    violations = 0
    details: list[dict] = []
    for scope in scopes[:check_limit]:
        try:
            states = {
                dm.MemoryState.CONFIRMED,
                dm.MemoryState.CANDIDATE,
                dm.MemoryState.SUPERSEDED,
            }
            page = await client.store.scan(
                scope, filters=dm.MemoryFilter(states=states), limit=50
            )
        except Exception as exc:  # noqa: BLE001 - audit boundary
            details.append(
                {"scope": scope.scope_key, "error": f"{type(exc).__name__}: {exc}"}
            )
            violations += 1
            continue
        checked += 1
        leaky = [
            r.memory_id for r in page.records if r.scope.scope_key != scope.scope_key
        ]
        if leaky:
            violations += 1
            details.append({"scope": scope.scope_key, "leaked_records": leaky[:5]})
    return {
        "checked_scopes": checked,
        "violations": violations,
        "passed": violations == 0,
        "details": details,
    }


async def run_load(
    dm: Any,
    dataset_path: Path,
    *,
    replay_twice: bool = False,
    isolation_check_scopes: int = 5,
    backend: str = "sqlite",
    **backend_kwargs: Any,
) -> dict:
    """Stream dataset through Doppel; return an audit report dict.

    Every write is classified (created/updated/duplicate/skipped/failed/
    unknown); failed and unknown statuses fail the run.
    """
    scope_counts: Counter[str] = Counter()
    scope_objects: dict[str, Any] = {}
    status_counts: Counter[str] = Counter()
    failures: list[dict[str, str]] = []

    db_kwargs = dict(backend_kwargs)
    if backend == "sqlite" and "database" not in db_kwargs:
        db_kwargs["database"] = str(
            Path(tempfile.mkdtemp(prefix="doppel-load-")) / "load.sqlite3"
        )
    client = dm.DoppelClient(backend=backend, **db_kwargs)
    started = time.perf_counter()

    async def _pass(*, collect_scopes: bool) -> int:
        """Run one ingest pass and return its own attempt count."""
        attempts = 0
        for raw in _iter_events(dataset_path):
            attempts += 1
            try:
                event = UnifiedEvent.model_validate(raw)
                payload = bridge_payload(event)
                if payload["errors"] or payload["scope"] is None:
                    raise ValueError("; ".join(payload["errors"]) or "missing scope")
                scope = dm.MemoryScope(**payload["scope"])
                message = dm.ChatMessage(**payload["message"])
                result = await client.ingest(scope, message)
                status = _status_value(result) or "unknown"
            except Exception as exc:  # noqa: BLE001 - benchmark audit boundary
                status = "failed"
                if len(failures) < 20:
                    failures.append(
                        {
                            "event_id": str(raw.get("eventId") or ""),
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )
            status_counts[status] += 1
            if collect_scopes and status in {"created", "updated"}:
                scope_counts[scope.scope_key] += 1
                scope_objects.setdefault(scope.scope_key, scope)
        return attempts

    try:
        source_events = await _pass(collect_scopes=True)
        first_pass_elapsed_ms = (time.perf_counter() - started) * 1000
        first_pass_attempts = source_events
        replay_attempts = 0
        replay_elapsed_ms = 0.0
        if replay_twice:
            replay_started = time.perf_counter()
            replay_attempts = await _pass(collect_scopes=False)
            replay_elapsed_ms = (time.perf_counter() - replay_started) * 1000

        isolation = await _isolation_check(
            dm,
            client,
            list(scope_objects.values()),
            check_limit=isolation_check_scopes,
        )
    finally:
        await client.close()
    total_elapsed_ms = (time.perf_counter() - started) * 1000
    per_scope = [c for c in scope_counts.values()]
    write_attempts = sum(status_counts.values())
    status_counts["unknown"] = status_counts.get("unknown", 0)
    failed = status_counts.get("failed", 0) + status_counts.get("unknown", 0)
    return {
        "runner": "doppel.load.v2",
        "dataset": dataset_path.name,
        "backend": backend,
        "generated_at": datetime.now(UTC).isoformat(),
        "source_events": source_events,
        "write_attempts": write_attempts,
        "pass_attempts": {
            "first_pass": first_pass_attempts,
            "replay": replay_attempts,
        },
        "status_counts": dict(sorted(status_counts.items())),
        "scope_count": len(scope_counts),
        "elapsed_ms": {
            "total": round(total_elapsed_ms, 3),
            "first_pass": round(first_pass_elapsed_ms, 3),
            "replay": round(replay_elapsed_ms, 3),
        },
        "throughput_events_per_sec": {
            "first_pass": round(first_pass_attempts / (first_pass_elapsed_ms / 1000), 1)
            if first_pass_elapsed_ms
            else 0.0,
            "replay": round(replay_attempts / (replay_elapsed_ms / 1000), 1)
            if replay_elapsed_ms
            else 0.0,
            "combined": round(write_attempts / (total_elapsed_ms / 1000), 1)
            if total_elapsed_ms
            else 0.0,
        },
        "per_scope_min": min(per_scope) if per_scope else 0,
        "per_scope_max": max(per_scope) if per_scope else 0,
        "per_scope_avg": round(sum(per_scope) / len(per_scope), 1)
        if per_scope
        else 0.0,
        "isolation": isolation,
        "failures": failures,
        "gate": {
            "ok": failed == 0 and isolation["passed"],
            "failed_writes": failed,
            "isolation_passed": isolation["passed"],
        },
    }
