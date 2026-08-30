"""Real-provider multi-episode temporal evolution evaluation.

Unlike ``graph-e2e --backend neo4j``, which pre-seeds a known-good graph without
an LLM, this runner sends every authoritative record through Doppel's real
``GraphitiSemanticIndex.index_record`` path.  It then executes the same generic
temporal/isolation query suite and reloads every candidate from the authoritative
Store.

The provider is protected by an explicit double switch, a content-addressed
cache, no automatic retries, and a sub-million-token hard budget.  The API key
is accepted only from the environment by the CLI and is never written to the
report or cache.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from doppel_eval.graph_e2e import (
    _cleanup_live_graph,
    _load_graph_helpers,
    _run_queries,
    _write_authoritative_records,
)
from doppel_eval.graphiti_provider import (
    BudgetedCachedGraphitiLLMClient,
    GraphitiProviderBudget,
)
from doppel_eval.graphiti_smoke import _preflight_neo4j

EVOLUTION_CONFIRM_ENV = "GRAPHITI_LIVE_EVOLUTION_CONFIRM"
EVOLUTION_CACHE_VERSION = "doppel.graphiti-evolution-cache.v1"
EVOLUTION_BUDGET = GraphitiProviderBudget(
    max_calls=60,
    max_input_tokens=600_000,
    max_output_tokens=61_440,
    max_total_tokens=700_000,
    max_output_tokens_per_call=1_024,
)


def validate_evolution_activation(
    *, model: str, base_url: str, api_key: str
) -> None:
    """Require an explicit confirmation plus complete provider configuration."""
    if os.environ.get(EVOLUTION_CONFIRM_ENV, "").strip().upper() != "YES":
        raise ValueError(
            f"live evolution requires {EVOLUTION_CONFIRM_ENV}=YES "
            "(explicit double switch)"
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
        raise ValueError(f"live evolution requires: {', '.join(missing)}")


async def run_graphiti_evolution(
    dm: Any,
    *,
    neo4j_uri: str,
    neo4j_user: str,
    neo4j_password: str,
    model: str,
    base_url: str,
    api_key: str,
    cache_dir: Path | None,
    keep_fixture: bool = False,
) -> dict[str, Any]:
    """Index seven facts with a real provider, then run temporal queries."""
    validate_evolution_activation(model=model, base_url=base_url, api_key=api_key)
    if not (neo4j_uri.strip() and neo4j_user.strip() and neo4j_password):
        raise ValueError(
            "live evolution requires GRAPHITI_EVAL_NEO4J_URI, "
            "GRAPHITI_EVAL_NEO4J_USER and GRAPHITI_EVAL_NEO4J_PASSWORD"
        )
    await _preflight_neo4j(neo4j_uri)

    from doppel_memory.graphiti_store import FastEmbedderClient, NoOpCrossEncoder
    from graphiti_core import Graphiti

    helpers = _load_graph_helpers()
    client = dm.DoppelClient(backend="memory")
    scopes = {
        owner: dm.MemoryScope(
            user_id=f"doppel-evolution-v1-{owner}",
            agent_id="memoecho-graph-evolution",
            platform="synthetic",
        )
        for owner in ("owner-a", "owner-b")
    }
    records = await _write_authoritative_records(dm, client, scopes)
    llm_client = BudgetedCachedGraphitiLLMClient(
        model=model,
        base_url=base_url,
        api_key=api_key,
        budget=EVOLUTION_BUDGET,
        cache_dir=cache_dir,
        max_retries=0,
        client_version=EVOLUTION_CACHE_VERSION,
    )
    graphiti: Any | None = None
    index: Any | None = None
    indexed: list[dict[str, Any]] = []
    scenarios: list[dict[str, Any]] = []
    projection: dict[str, Any] = {}
    privacy: dict[str, Any] = {}
    hard_failure = ""
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
        group_ids = [scope.scope_key for scope in scopes.values()]
        # Stable evaluation scopes make provider cache keys reusable. Remove only
        # those exact hashed scopes before and after every run.
        await _cleanup_live_graph(graphiti, group_ids)
        index = helpers.GraphitiSemanticIndex(client.store, graphiti_client=graphiti)
        for record in records:
            try:
                result = await index.index_record(record)
                indexed.append(
                    {
                        "memory_id": result.memory_id,
                        "episode_id": result.episode_id,
                        "scope_key": result.scope_key,
                        "status": result.status.value,
                    }
                )
            except Exception as exc:  # noqa: BLE001 - report exact product boundary
                hard_failure = (
                    f"index {record.memory_id}: {type(exc).__name__}: {exc}"
                )
                break

        if not hard_failure:
            projection = await _read_evolution_projection(
                graphiti,
                indexed=indexed,
                group_ids=group_ids,
            )
            privacy = await _read_privacy_projection(
                graphiti,
                group_ids=group_ids,
                raw_subject_ids=[scope.user_id for scope in scopes.values()],
            )
            scenarios = await _run_queries(dm, client, index, scopes)
    except Exception as exc:  # noqa: BLE001 - preserve a structured live report
        hard_failure = hard_failure or f"{type(exc).__name__}: {exc}"
    finally:
        if graphiti is not None:
            try:
                if not keep_fixture:
                    await _cleanup_live_graph(
                        graphiti, [scope.scope_key for scope in scopes.values()]
                    )
                    cleanup_performed = True
            finally:
                await graphiti.close()
        await llm_client.aclose()
        await client.close()

    usage = llm_client.ledger.report()
    passed = sum(bool(item.get("passed")) for item in scenarios)
    gate = _evolution_gate(
        hard_failure=hard_failure,
        indexed=indexed,
        records=records,
        scenarios=scenarios,
        projection=projection,
        privacy=privacy,
        cleanup_performed=cleanup_performed,
        keep_fixture=keep_fixture,
        usage=usage,
    )
    return {
        "runner": "doppel.graphiti-evolution.v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": "live-neo4j-live-provider",
        "label": (
            "authoritative Store -> real Graphiti extraction/evolution -> temporal "
            "candidate retrieval -> exact Store revalidation; synthetic data; no QQ"
        ),
        "provider": {
            "model": model,
            "base_url_host": _host_only(base_url),
            "key_configured": bool(api_key),
            "key_persisted": False,
            "budget": EVOLUTION_BUDGET.__dict__,
            "logical_calls": llm_client.logical_calls,
            "prompt_names": llm_client.prompt_names,
        },
        "backend": {
            "neo4j_endpoint_configured": True,
            "credentials_persisted": False,
            "isolated_scope_count": len(scopes),
            "fixture_cleanup_performed": cleanup_performed,
            "fixture_retained": keep_fixture,
        },
        "usage": usage,
        "hard_failure": hard_failure,
        "indexed": indexed,
        "projection": projection,
        "privacy": privacy,
        "summary": {
            "records": len(records),
            "indexed_records": len(indexed),
            "scenarios": len(scenarios),
            "passed_scenarios": passed,
        },
        "gate": gate,
        "scenarios": scenarios,
    }


async def _read_evolution_projection(
    graphiti: Any,
    *,
    indexed: list[dict[str, Any]],
    group_ids: list[str],
) -> dict[str, Any]:
    result = await graphiti.driver.execute_query(
        "MATCH (n:Entity) WHERE n.group_id IN $group_ids "
        "WITH count(DISTINCT n) AS nodes "
        "OPTIONAL MATCH ()-[edge:RELATES_TO]->() "
        "WHERE edge.group_id IN $group_ids "
        "RETURN nodes, count(edge) AS edges",
        group_ids=group_ids,
    )
    row = next(iter(result.records)).data()
    edges_by_memory: dict[str, int] = {}
    fallback_edges_by_memory: dict[str, int] = {}
    for item in indexed:
        edge_result = await graphiti.driver.execute_query(
            "MATCH ()-[edge:RELATES_TO]->() "
            "WHERE edge.group_id = $group_id "
            "AND $episode_id IN coalesce(edge.episodes, []) "
            "RETURN count(edge) AS edges, "
            "count(CASE WHEN edge.name = 'DOPPEL_MEMORY_FALLBACK' "
            "THEN 1 END) AS fallback_edges",
            group_id=item["scope_key"],
            episode_id=item["episode_id"],
        )
        edge_row = next(iter(edge_result.records))
        edges_by_memory[item["memory_id"]] = int(edge_row["edges"] or 0)
        fallback_edges_by_memory[item["memory_id"]] = int(
            edge_row["fallback_edges"] or 0
        )
    return {
        "nodes": int(row.get("nodes") or 0),
        "edges": int(row.get("edges") or 0),
        "edges_by_memory": edges_by_memory,
        "fallback_edges": sum(fallback_edges_by_memory.values()),
        "fallback_edges_by_memory": fallback_edges_by_memory,
    }


async def _read_privacy_projection(
    graphiti: Any,
    *,
    group_ids: list[str],
    raw_subject_ids: list[str],
) -> dict[str, Any]:
    result = await graphiti.driver.execute_query(
        "MATCH (n) WHERE n.group_id IN $group_ids "
        "WITH n, [key IN keys(n) WHERE any(raw IN $raw_ids "
        "WHERE coalesce(toStringOrNull(n[key]), '') CONTAINS raw)] "
        "AS leaking_keys "
        "RETURN count(CASE WHEN size(leaking_keys) > 0 THEN 1 END) AS leaks, "
        "count(CASE WHEN n:Entity AND n.name STARTS WITH 'DoppelSubject-' "
        "THEN 1 END) AS pseudonymous_subjects",
        group_ids=group_ids,
        raw_ids=raw_subject_ids,
    )
    row = next(iter(result.records)).data()
    return {
        "raw_subject_leaks": int(row.get("leaks") or 0),
        "pseudonymous_subjects": int(row.get("pseudonymous_subjects") or 0),
    }


def _evolution_gate(
    *,
    hard_failure: str,
    indexed: list[dict[str, Any]],
    records: list[Any],
    scenarios: list[dict[str, Any]],
    projection: dict[str, Any],
    privacy: dict[str, Any],
    cleanup_performed: bool,
    keep_fixture: bool,
    usage: dict[str, Any],
) -> dict[str, Any]:
    edges_by_memory = projection.get("edges_by_memory") or {}
    checks = {
        "no_hard_failure": not hard_failure,
        "all_records_indexed": len(indexed) == len(records),
        "every_record_has_graph_provenance": (
            len(edges_by_memory) == len(records)
            and all(int(value) > 0 for value in edges_by_memory.values())
        ),
        "all_temporal_queries_passed": (
            bool(scenarios) and all(bool(item.get("passed")) for item in scenarios)
        ),
        "raw_subject_ids_absent": privacy.get("raw_subject_leaks") == 0,
        "pseudonymous_subjects_present": int(
            privacy.get("pseudonymous_subjects") or 0
        ) >= 2,
        "fixture_cleanup_performed": cleanup_performed or keep_fixture,
        "within_budget": bool(usage.get("within_budget")),
    }
    return {"ok": all(checks.values()), "checks": checks}


def _host_only(base_url: str) -> str:
    from urllib.parse import urlsplit

    parsed = urlsplit(str(base_url or "").strip())
    return parsed.netloc or ""
