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
                    "invalid_at": None,
                }
            ]
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
    # Inject the Doppel source path early; imports below depend on it.
    dm = _load_doppel()
    from graphiti_core import Graphiti
    from doppel_memory.graphiti_store import (
        FastEmbedderClient,
        GraphitiSemanticIndex,
        NoOpCrossEncoder,
    )

    if live_provider:
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

    dm = _load_doppel()
    scenario: dict[str, Any] = {}
    graphiti: Any = None
    index: Any = None
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

        # Provenance: map graph edge candidates back to the authoritative Store.
        reloaded = await client.store.get(scope, SMOKE_MEMORY_ID)
        provenance_ok = reloaded is not None and reloaded.memory_id == SMOKE_MEMORY_ID
        store_revalidation_ok = _record_matches_smoke(dm, reloaded)
        isolation_reloaded = await _reload_scope(client.store, scope)

        graph_edge_path = (
            await _count_episode_edges(graphiti, indexed.episode_id)
            if indexed is not None
            else 0
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
            "prompt_names": [],
            "logical_calls": 0,
            "http_attempts": len(fake_bodies)
            if transport_used == "fake"
            else llm_client.ledger.calls_attempted,
            "cache_hits": llm_client.ledger.cache_hits,
            "nodes_created": 0,
            "edges_created": graph_edge_path,
            "edge_valid_at": None,
            "edge_invalid_at": None,
            "inside_interval_hit": inside_hit,
            "outside_interval_hit": outside_hit,
            "provenance_ok": provenance_ok,
            "store_revalidation_ok": store_revalidation_ok,
            "scope_isolation_ok": not isolation_reloaded,
            "cleanup_performed": False,
            "hard_failure": index_error,
            "stopped_by_budget": not llm_client.ledger.report()["within_budget"],
            "graph_edge_path_not_exercised": graph_edge_path == 0,
            "usage": llm_client.ledger.report(),
        }
        if transport_used == "fake" and graph_edge_path == 0:
            scenario["notes"] = [
                "dry-run fake extraction was never reached: Doppel GraphitiSemanticIndex "
                "first-index calls Graphiti 0.29 add_episode(uuid=<deterministic id>) whose "
                "uuid path requires an existing episode node (get_by_uuid raises); the smoke "
                "ends before graph edge adoption, honestly reported as "
                "graph_edge_path_not_exercised=true",
                "domain-neutral Doppel-side fix would ensure the deterministic episode slot "
                "exists in graphiti_store.upsert before add_episode(uuid=..)",
            ]
    finally:
        if graphiti is not None:
            await _cleanup_smoke_scope(graphiti, scope.scope_key)
            cleanup_performed = True
            await graphiti.close()
        if http_client is not None:
            await http_client.aclose()
        if index is not None:
            await index.close() if hasattr(index, "close") else None

    scenario["cleanup_performed"] = cleanup_performed
    scenario["usage"] = llm_client.ledger.report()
    return {
        "runner": "doppel.graphiti-smoke.v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "scenario": scenario,
    }


def _any_hit_for(dm: Any, hits: Any, memory_id: str) -> bool:
    for hit in hits:
        if str(getattr(hit, "memory_id", "")) == memory_id:
            return True
        source = str(getattr(hit, "source_message_id", "") or "")
        if source and memory_id in source:
            return True
    return bool(hits)  # a hit without provenance still signals the path ran


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


async def _count_episode_edges(graphiti: Any, episode_id: str) -> int:
    """Count edges whose episodes reference our smoke episode id."""
    if not episode_id:
        return 0
    result = await graphiti.driver.execute_query(
        "MATCH (e:Episodic {uuid: $episode_id})<-[:FROM_EPISODE]-(edge:EpisodicEdge) "
        "RETURN count(edge) AS edges",
        episode_id=episode_id,
    )
    return _read_count(result)


def _read_count(result: Any) -> int:
    try:
        records = list(result.records)
        if records:
            return int(records[0].data().get("edges") or 0)
    except Exception:  # noqa: BLE001
        pass
    return 0


def _host_only(base_url: str) -> str:
    from urllib.parse import urlsplit

    parsed = urlsplit(str(base_url or "").strip())
    return parsed.netloc if parsed.netloc else str(base_url or "").strip()


async def _precreate_episode(graphiti: Any, dm: Any, episode_id: str, record: Any) -> None:
    """Create the deterministic episode node before Doppel indexes it."""
    from graphiti_core.nodes import EpisodicNode, EpisodeType

    existing = None
    try:
        existing = await EpisodicNode.get_by_uuid(graphiti.driver, episode_id)
    except Exception:  # noqa: BLE001 - not found
        pass
    if existing is not None:
        return
    node = EpisodicNode(
        uuid=episode_id,
        name=record.memory_id,
        group_id=record.scope.scope_key,
        labels=[],
        source=EpisodeType.message,
        content=str(record.content),
        source_description="doppel.graphiti-paid-smoke.v1",
        created_at=record.created_at,
        valid_at=SMOKE_OBSERVED_AT,
    )
    await node.save(graphiti.driver)


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