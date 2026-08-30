"""Single-episode paid smoke runner (offline by default, double switch to go live).

Pipeline: authoritative Store put -> GraphitiSemanticIndex.index_record ->
real Neo4j -> search_at inside/after interval -> authoritative Store reload
-> exact scope cleanup.

Default mode is a fully offline dry-run with an in-process fake transport.
Going live requires BOTH ``--live-provider`` AND
``GRAPHITI_LIVE_SMOKE_CONFIRM=YES``, plus non-empty ``DOPPEL_API_KEY``,
``DOPPEL_MODEL`` and ``DOPPEL_OPENAI_BASE_URL``.  The API key cannot be
passed on the CLI (avoids shell history).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from doppel_eval.graphiti_probe import SCHEMA_RE, _minimal_for_schema
from doppel_eval.graphiti_provider import (
    BudgetedCachedGraphitiLLMClient,
    GraphitiProviderBudget,
)

logger = logging.getLogger(__name__)

SMOKE_MEMORY_ID = "graphiti-paid-smoke-temporary-beijing-v1"
SMOKE_CONTENT = "2025年1月1日至2025年3月31日，我在北京临时出差居住。"
SMOKE_VALID_FROM = datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC)
SMOKE_VALID_TO = datetime(2025, 3, 31, 23, 59, 59, tzinfo=UTC)
SMOKE_OBSERVED_AT = datetime(2024, 12, 20, 8, 0, 0, tzinfo=UTC)
SMOKE_SCOPE_PREFIX = "graphiti-smoke-eval-"

SMOKE_BUDGET = GraphitiProviderBudget(
    max_calls=6,
    max_input_tokens=18_000,
    max_output_tokens=6_144,
    max_total_tokens=24_144,
    max_output_tokens_per_call=1_024,
)

LIVE_CONFIRM_ENV = "GRAPHITI_LIVE_SMOKE_CONFIRM"


class _SmokeSchemaTransport:
    """Deterministic, domain-neutral fake LLM for the smoke dry-run.

    Builds minimal legal replies from the injected schema shape (entity
    extraction / edge extraction / edge timestamps), using generic names;
    no residence/travel/job-specific logic.  Returns non-self-loop edges so
    the Graphiti edge/adoption path can actually run.
    """

    def __init__(self) -> None:
        self.bodies: list[dict[str, Any]] = []

    async def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        self.bodies.append(body)
        minimal = _smoke_minimal(body.get("messages") or [])
        usage = {
            "prompt_tokens": 120,
            "completion_tokens": 40,
            "total_tokens": 160,
        }
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps(minimal, ensure_ascii=False)}}],
                "usage": usage,
            },
        )


def _smoke_minimal(messages: list[dict[str, str]]) -> dict[str, Any]:
    schema = None
    for message in reversed(messages):
        content = str(message.get("content") or "")
        match = SCHEMA_RE.search(content)
        if match:
            try:
                schema = json.loads(match.group(1))
            except ValueError:
                schema = None
            break
    if schema is None:
        return {}
    props = schema.get("properties") or {}
    if "extracted_entities" in props:
        return {
            "extracted_entities": [
                {"name": "EntityA", "entity_type_id": 0, "episode_indices": [0]},
                {"name": "EntityB", "entity_type_id": 0, "episode_indices": [0]},
            ]
        }
    if "edges" in props:
        return {
            "edges": [
                {
                    "source_entity_name": "EntityA",
                    "target_entity_name": "EntityB",
                    "relation_type": "RELATED_TO",
                    "fact": "EntityA and EntityB are related",
                    "valid_at": None,
                    "invalid_at": None,
                    "episode_indices": [0],
                }
            ]
        }
    if "timestamps" in props:
        return {
            "timestamps": [
                {
                    "valid_at": "2025-01-01T00:00:00Z",
                    "invalid_at": "2025-03-31T23:59:59Z",
                }
            ]
        }
    if "valid_at" in props and "invalid_at" in props:
        return {
            "valid_at": "2025-01-01T00:00:00Z",
            "invalid_at": "2025-03-31T23:59:59Z",
        }
    return _minimal_for_schema(schema)


def smoke_scope(dm: Any) -> Any:
    """A fixed, evaluation-only scope that is safe to delete exactly."""
    return dm.MemoryScope(
        user_id=f"{SMOKE_SCOPE_PREFIX}owner",
        agent_id="memoecho-graph-smoke",
        platform="synthetic",
        chat_type="private",
        chat_id=f"{SMOKE_SCOPE_PREFIX}chat",
    )


def smoke_record(dm: Any, scope: Any) -> Any:
    """The fixed authoritative record; stable ids make provider requests cacheable."""
    return dm.MemoryRecord(
        memory_id=SMOKE_MEMORY_ID,
        kind=dm.MemoryKind.FACT,
        scope=scope,
        content=SMOKE_CONTENT,
        actor="owner",
        authority=dm.FactAuthority.HUMAN_SELF,
        state=dm.MemoryState.CONFIRMED,
        importance=0.7,
        tags=["personal-memory"],
        extractor="doppel.graphiti-paid-smoke.v1",
        metadata={
            "personal_memory_type": "episode",
            "temporal_status": "historical",
            "valid_from": SMOKE_VALID_FROM.isoformat(),
            "valid_to": SMOKE_VALID_TO.isoformat(),
            "observed_at": SMOKE_OBSERVED_AT.isoformat(),
            "subject": "owner",
            "subject_id": scope.user_id,
            "evidence": [
                {
                    "evidence_id": f"{SMOKE_MEMORY_ID}:ev1",
                    "at": SMOKE_OBSERVED_AT.isoformat(),
                }
            ],
        },
    )


async def run_smoke(
    *,
    neo4j_uri: str,
    neo4j_user: str,
    neo4j_password: str,
    live_provider: bool = False,
    model: str = "",
    base_url: str = "",
    api_key: str = "",
    cache_dir: Path | None = None,
) -> dict[str, Any]:
    """Run the single-episode smoke; dry-run (fake transport) by default."""
    _validate_provider_activation(
        live_provider=live_provider,
        model=model,
        base_url=base_url,
        api_key=api_key,
    )
    await _preflight_neo4j(neo4j_uri)

    # Load product dependencies only after the no-cost live-provider guards pass.
    dm = _load_doppel()
    from graphiti_core import Graphiti
    from doppel_memory.graphiti_store import (
        FastEmbedderClient,
        GraphitiSemanticIndex,
        NoOpCrossEncoder,
    )

    if live_provider:
        http_client: httpx.AsyncClient | None = None
        transport_used = "live"
        fake_bodies: list[dict[str, Any]] = []
    else:
        transport = _SmokeSchemaTransport()
        http_client = httpx.AsyncClient(transport=httpx.MockTransport(transport.handler))
        transport_used = "fake"
        fake_bodies = transport.bodies

    llm_client = BudgetedCachedGraphitiLLMClient(
        model=model or "deepseek-v4-flash",
        base_url=base_url or "https://api.deepseek.com",
        api_key=api_key,
        http_client=http_client,
        budget=SMOKE_BUDGET,
        cache_dir=cache_dir,
        max_retries=0,
    )

    scenario: dict[str, Any] = {}
    graphiti: Any = None
    index: Any = None
    scope: Any = None
    cleanup_performed = False
    try:
        graphiti = Graphiti(
            uri=neo4j_uri,
            user=neo4j_user,
            password=neo4j_password,
            llm_client=llm_client,
            embedder=FastEmbedderClient(),
            cross_encoder=NoOpCrossEncoder(),
        )
        await graphiti.build_indices_and_constraints(delete_existing=False)

        client = dm.DoppelClient(backend="memory")
        scope = smoke_scope(dm)
        record = smoke_record(dm, scope)

        await client.put(record)
        authoritative = await client.store.get(scope, SMOKE_MEMORY_ID)
        if authoritative is None:
            raise RuntimeError("authoritative Store reload after put returned None")

        index = GraphitiSemanticIndex(client.store, graphiti_client=graphiti)
        indexed = None
        index_error = ""
        try:
            indexed = await index.index_record(record)
            if indexed.status.value not in {"indexed", "skipped"}:
                index_error = f"index_record status: {indexed.status}"
        except Exception as exc:  # noqa: BLE001 - index error is reported, not fatal
            index_error = f"{type(exc).__name__}: {exc}"

        inside_hit = False
        outside_hit = False
        if indexed is not None and not index_error:
            inside = await index.search_at(
                "我在北京临时出差居住",
                [scope],
                valid_at=datetime(2025, 2, 15, tzinfo=UTC),
                limit=5,
            )
            outside = await index.search_at(
                "我在北京临时出差居住",
                [scope],
                valid_at=datetime(2025, 6, 15, tzinfo=UTC),
                limit=5,
            )
            inside_hit = _any_hit_for(dm, inside, SMOKE_MEMORY_ID)
            outside_hit = _any_hit_for(dm, outside, SMOKE_MEMORY_ID)

        # Provenance: map the exact graph candidate back to the authoritative Store.
        reloaded = await client.store.get(scope, SMOKE_MEMORY_ID)
        provenance_ok = (
            inside_hit
            and reloaded is not None
            and reloaded.memory_id == SMOKE_MEMORY_ID
        )
        store_revalidation_ok = _record_matches_smoke(dm, reloaded)
        isolation_reloaded = await _reload_scope(client.store, scope)

        graph_stats = (
            await _read_graph_projection(
                graphiti,
                group_id=scope.scope_key,
                episode_id=indexed.episode_id,
            )
            if indexed is not None
            else _empty_graph_projection()
        )
        scenario = {
            "mode": "live-provider" if transport_used == "live" else "dry-run-fake",
            "live_provider": live_provider,
            "confirmation_present": (
                os.environ.get(LIVE_CONFIRM_ENV, "").strip().upper() == "YES"
            ),
            "key_configured": bool(api_key),
            "key_persisted": False,
            "model": llm_client.model,
            "base_url_host": _host_only(base_url) if base_url else "",
            "budget": SMOKE_BUDGET.__dict__,
            "prompt_names": llm_client.prompt_names,
            "logical_calls": llm_client.logical_calls,
            "http_attempts": len(fake_bodies)
            if transport_used == "fake"
            else llm_client.ledger.calls_attempted,
            "cache_hits": llm_client.ledger.cache_hits,
            "nodes_created": graph_stats["nodes"],
            "edges_created": graph_stats["edges"],
            "edge_valid_at": graph_stats["valid_at"],
            "edge_invalid_at": graph_stats["invalid_at"],
            "inside_interval_hit": inside_hit,
            "outside_interval_hit": outside_hit,
            "provenance_ok": provenance_ok,
            "store_revalidation_ok": store_revalidation_ok,
            "scope_isolation_ok": not isolation_reloaded,
            "cleanup_performed": False,
            "hard_failure": index_error,
            "stopped_by_budget": not llm_client.ledger.report()["within_budget"],
            "graph_edge_path_not_exercised": graph_stats["edges"] == 0,
            "usage": llm_client.ledger.report(),
        }
    finally:
        if graphiti is not None:
            try:
                if scope is not None:
                    await _cleanup_smoke_scope(graphiti, scope.scope_key)
                    cleanup_performed = True
            finally:
                await graphiti.close()
        if http_client is not None:
            await http_client.aclose()
        if index is not None:
            await index.close() if hasattr(index, "close") else None

    scenario["cleanup_performed"] = cleanup_performed
    scenario["usage"] = llm_client.ledger.report()
    scenario["gate"] = _smoke_gate(scenario)
    return {
        "runner": "doppel.graphiti-smoke.v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "scenario": scenario,
    }


def _validate_provider_activation(
    *, live_provider: bool, model: str, base_url: str, api_key: str
) -> None:
    """Enforce the two explicit switches before product or network setup."""
    if not live_provider:
        return
    confirmation = os.environ.get(LIVE_CONFIRM_ENV, "").strip().upper() == "YES"
    if not confirmation:
        raise ValueError(
            f"live provider requires {LIVE_CONFIRM_ENV}=YES (explicit double switch)"
        )
    missing = [
        name
        for name, value in (
            ("DOPPEL_API_KEY", api_key),
            ("DOPPEL_MODEL", model),
            ("DOPPEL_OPENAI_BASE_URL", base_url),
        )
        if not str(value or "").strip()
    ]
    if missing:
        raise ValueError(f"live provider requires: {', '.join(missing)}")


async def _preflight_neo4j(uri: str, *, timeout_seconds: float = 5.0) -> None:
    """Fail fast before Graphiti's Neo4j driver starts transaction retries."""
    from urllib.parse import urlsplit

    parsed = urlsplit(str(uri or "").strip())
    host = parsed.hostname
    port = parsed.port or 7687
    if not host:
        raise ValueError("Neo4j URI must include a hostname")
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=timeout_seconds
        )
    except (TimeoutError, OSError) as exc:
        raise RuntimeError(
            f"Neo4j preflight failed: {host}:{port} is not reachable"
        ) from exc
    del reader
    writer.close()
    await writer.wait_closed()


def _any_hit_for(dm: Any, hits: Any, memory_id: str) -> bool:
    for hit in hits:
        if str(getattr(hit, "memory_id", "")) == memory_id:
            return True
        source = str(getattr(hit, "source_message_id", "") or "")
        if source and memory_id in source:
            return True
    return False


def _record_matches_smoke(dm: Any, record: Any) -> bool:
    if record is None:
        return False
    return str(record.metadata.get("personal_memory_type", "")) == "episode" and (
        "北京" in str(record.content)
    )


async def _reload_scope(store: Any, scope: Any) -> bool:
    """Return True when the exact scope holds exactly the smoke record (no leaks)."""
    page = await store.scan(scope, limit=50)
    foreign = [
        record.memory_id
        for record in page.records
        if record.memory_id != SMOKE_MEMORY_ID
    ]
    return bool(foreign)


def _empty_graph_projection() -> dict[str, Any]:
    return {"nodes": 0, "edges": 0, "valid_at": None, "invalid_at": None}


async def _read_graph_projection(
    graphiti: Any, *, group_id: str, episode_id: str
) -> dict[str, Any]:
    """Inspect Graphiti 0.29's actual Entity/RELATES_TO projection."""
    if not episode_id or not group_id:
        return _empty_graph_projection()
    result = await graphiti.driver.execute_query(
        "OPTIONAL MATCH (entity:Entity {group_id: $group_id}) "
        "WITH count(DISTINCT entity) AS nodes "
        "OPTIONAL MATCH ()-[edge:RELATES_TO]->() "
        "WHERE edge.group_id = $group_id "
        "AND $episode_id IN coalesce(edge.episodes, []) "
        "RETURN nodes, count(edge) AS edges, min(edge.valid_at) AS valid_at, "
        "max(edge.invalid_at) AS invalid_at",
        group_id=group_id,
        episode_id=episode_id,
    )
    try:
        records = list(result.records)
        if not records:
            return _empty_graph_projection()
        data = records[0].data()
        return {
            "nodes": int(data.get("nodes") or 0),
            "edges": int(data.get("edges") or 0),
            "valid_at": _json_datetime(data.get("valid_at")),
            "invalid_at": _json_datetime(data.get("invalid_at")),
        }
    except Exception:  # noqa: BLE001 - report an empty projection and fail the gate
        logger.warning("failed to inspect smoke graph projection", exc_info=True)
        return _empty_graph_projection()


def _json_datetime(value: Any) -> str | None:
    if value is None:
        return None
    native = value.to_native() if hasattr(value, "to_native") else value
    if hasattr(native, "isoformat"):
        return str(native.isoformat())
    return str(native)


def _smoke_gate(scenario: dict[str, Any]) -> dict[str, Any]:
    checks = {
        "no_hard_failure": not bool(scenario.get("hard_failure")),
        "graph_nodes_created": int(scenario.get("nodes_created") or 0) >= 2,
        "graph_edge_created": int(scenario.get("edges_created") or 0) >= 1,
        "edge_valid_from_preserved": str(scenario.get("edge_valid_at") or "").startswith(
            "2025-01-01"
        ),
        "edge_valid_to_preserved": str(scenario.get("edge_invalid_at") or "").startswith(
            "2025-03-31"
        ),
        "inside_interval_hit": bool(scenario.get("inside_interval_hit")),
        "outside_interval_rejected": not bool(scenario.get("outside_interval_hit")),
        "provenance_ok": bool(scenario.get("provenance_ok")),
        "store_revalidation_ok": bool(scenario.get("store_revalidation_ok")),
        "scope_isolation_ok": bool(scenario.get("scope_isolation_ok")),
        "cleanup_performed": bool(scenario.get("cleanup_performed")),
        "within_budget": bool((scenario.get("usage") or {}).get("within_budget")),
    }
    return {"ok": all(checks.values()), "checks": checks}


def _host_only(base_url: str) -> str:
    from urllib.parse import urlsplit

    parsed = urlsplit(str(base_url or "").strip())
    return parsed.netloc if parsed.netloc else str(base_url or "").strip()


async def _cleanup_smoke_scope(graphiti: Any, scope_key: str) -> None:
    if not re.fullmatch(r"dpl_[0-9a-f]{64}", scope_key):
        logger.warning("refusing to clean a non-smoke scope: %s", scope_key)
        return
    await graphiti.driver.execute_query(
        "MATCH (n) WHERE n.group_id = $group_id DETACH DELETE n",
        group_id=scope_key,
    )


def _load_doppel() -> Any:
    import sys

    path = os.environ.get("DOPPEL_IMPORT_PATH", "").strip()
    if path:
        path_obj = Path(path)
        if path_obj.is_dir() and str(path_obj) not in sys.path:
            sys.path.insert(0, str(path_obj))
    try:
        import doppel_memory  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"doppel_memory not importable: {exc}") from exc
    return sys.modules["doppel_memory"]
