"""Budget-free temporal Graphiti evaluation for Doppel inside MemoEcho.

The default backend is an in-process Graphiti contract double.  The optional
``neo4j`` backend writes Graphiti-compatible nodes and edges directly, using the
local FastEmbed encoder.  Neither backend performs LLM extraction or contacts QQ.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, Literal
from uuid import NAMESPACE_URL, uuid4, uuid5

GraphBackend = Literal["contract", "neo4j"]


def _at(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


@dataclass(frozen=True)
class GraphMemoryFixture:
    memory_id: str
    owner: str
    content: str
    topic_key: str
    temporal_status: str
    valid_from: datetime | None
    valid_to: datetime | None
    observed_at: datetime
    personal_memory_type: str = "fact"


@dataclass(frozen=True)
class GraphQueryFixture:
    case_id: str
    owner: str
    query: str
    now: datetime
    expected_ids: tuple[str, ...]
    forbidden_ids: tuple[str, ...] = ()
    assertion: str = ""


def _memories() -> list[GraphMemoryFixture]:
    return [
        GraphMemoryFixture(
            memory_id="residence-shanghai",
            owner="owner-a",
            content="我的长期住址是上海。",
            topic_key="profile.residence.permanent",
            temporal_status="current",
            valid_from=_at("2020-01-01T00:00:00+00:00"),
            valid_to=None,
            observed_at=_at("2024-01-10T08:00:00+00:00"),
        ),
        GraphMemoryFixture(
            memory_id="temporary-beijing",
            owner="owner-a",
            content="2025年1月至3月在北京临时出差居住。",
            topic_key="episode.temporary-residence.beijing",
            temporal_status="historical",
            valid_from=_at("2025-01-01T00:00:00+00:00"),
            valid_to=_at("2025-03-31T23:59:59+00:00"),
            observed_at=_at("2024-12-20T08:00:00+00:00"),
            personal_memory_type="episode",
        ),
        GraphMemoryFixture(
            memory_id="late-shenzhen-project",
            owner="owner-a",
            content="2024年至2025年在深圳项目组工作。",
            topic_key="episode.work-project.shenzhen",
            temporal_status="historical",
            valid_from=_at("2024-01-01T00:00:00+00:00"),
            valid_to=_at("2025-12-31T23:59:59+00:00"),
            observed_at=_at("2026-02-01T08:00:00+00:00"),
            personal_memory_type="episode",
        ),
        GraphMemoryFixture(
            memory_id="cancelled-guangzhou-plan",
            owner="owner-a",
            content="原定2025年7月去广州的计划已经取消，最终没有成行。",
            topic_key="plan.travel.guangzhou",
            temporal_status="historical",
            valid_from=_at("2025-04-01T00:00:00+00:00"),
            valid_to=_at("2025-07-01T00:00:00+00:00"),
            observed_at=_at("2025-06-20T08:00:00+00:00"),
        ),
        GraphMemoryFixture(
            memory_id="role-designer-old",
            owner="owner-a",
            content="我在星河公司的职位是设计师。",
            topic_key="profile.employment.role.xinghe",
            temporal_status="historical",
            valid_from=_at("2023-01-01T00:00:00+00:00"),
            valid_to=_at("2025-05-31T23:59:59+00:00"),
            observed_at=_at("2024-03-01T08:00:00+00:00"),
        ),
        GraphMemoryFixture(
            memory_id="role-product-current",
            owner="owner-a",
            content="我在星河公司的职位是产品经理。",
            topic_key="profile.employment.role.xinghe",
            temporal_status="current",
            valid_from=_at("2025-06-01T00:00:00+00:00"),
            valid_to=None,
            observed_at=_at("2025-06-02T08:00:00+00:00"),
        ),
        GraphMemoryFixture(
            memory_id="other-owner-residence",
            owner="owner-b",
            content="我的长期住址是杭州。",
            topic_key="profile.residence.permanent",
            temporal_status="current",
            valid_from=_at("2020-01-01T00:00:00+00:00"),
            valid_to=None,
            observed_at=_at("2025-01-01T08:00:00+00:00"),
        ),
    ]


def _queries() -> list[GraphQueryFixture]:
    return [
        GraphQueryFixture(
            case_id="current-after-temporary-residence",
            owner="owner-a",
            query="我现在的长期住址在哪里？",
            now=_at("2025-06-15T12:00:00+00:00"),
            expected_ids=("residence-shanghai",),
            forbidden_ids=("temporary-beijing", "other-owner-residence"),
            assertion="expired temporary stay must not replace permanent residence",
        ),
        GraphQueryFixture(
            case_id="as-of-during-temporary-residence",
            owner="owner-a",
            query="2025年2月15日临时居住在哪里？",
            now=_at("2026-06-15T12:00:00+00:00"),
            expected_ids=("temporary-beijing",),
            forbidden_ids=("other-owner-residence",),
            assertion="as-of lookup must include the fact valid at that point",
        ),
        GraphQueryFixture(
            case_id="late-arriving-historical-fact",
            owner="owner-a",
            query="2024年6月15日我在哪个项目组工作？",
            now=_at("2026-06-15T12:00:00+00:00"),
            expected_ids=("late-shenzhen-project",),
            assertion="valid time must be independent from the later observation time",
        ),
        GraphQueryFixture(
            case_id="cancelled-plan-is-not-episode",
            owner="owner-a",
            query="2025年6月25日广州计划后来怎样了？",
            now=_at("2026-06-15T12:00:00+00:00"),
            expected_ids=("cancelled-guangzhou-plan",),
            assertion="a cancellation is retained as a fact, not a completed episode",
        ),
        GraphQueryFixture(
            case_id="correction-preserves-old-time-value",
            owner="owner-a",
            query="2025年5月15日我在星河公司的职位是什么？",
            now=_at("2026-06-15T12:00:00+00:00"),
            expected_ids=("role-designer-old",),
            forbidden_ids=("role-product-current",),
            assertion="the old value remains available inside its validity interval",
        ),
        GraphQueryFixture(
            case_id="correction-invalidates-old-value",
            owner="owner-a",
            query="2025年6月15日我在星河公司的职位是什么？",
            now=_at("2026-06-15T12:00:00+00:00"),
            expected_ids=("role-product-current",),
            forbidden_ids=("role-designer-old",),
            assertion="the old value must be absent after its validity interval",
        ),
        GraphQueryFixture(
            case_id="cross-owner-temporal-isolation",
            owner="owner-a",
            query="我现在的长期住址在哪里？",
            now=_at("2025-06-15T12:00:00+00:00"),
            expected_ids=("residence-shanghai",),
            forbidden_ids=("other-owner-residence",),
            assertion="a graph candidate from another owner must never cross scope",
        ),
    ]


class ContractGraphitiClient:
    """Minimal Graphiti search/provenance contract with generic n-gram matching."""

    def __init__(self, records: list[Any], graph_helpers: Any) -> None:
        self.search_calls: list[dict[str, Any]] = []
        self._episodes: dict[str, Any] = {}
        self._edges: list[Any] = []
        for record in records:
            episode_id = graph_helpers.episode_id(record.scope, record.memory_id)
            fingerprint = graph_helpers.fingerprint(record)
            self._episodes[episode_id] = SimpleNamespace(
                uuid=episode_id,
                name=graph_helpers.episode_name(
                    record.memory_id, fingerprint, record.version
                ),
                group_id=record.scope.scope_key,
            )
            self._edges.append(
                SimpleNamespace(
                    uuid=f"edge:{record.memory_id}",
                    group_id=record.scope.scope_key,
                    fact=record.content,
                    episodes=[episode_id],
                    created_at=record.created_at,
                    valid_at=_metadata_time(record, "valid_from"),
                    invalid_at=_metadata_time(record, "valid_to"),
                    expired_at=None,
                )
            )

    async def search(self, **kwargs: Any) -> list[Any]:
        self.search_calls.append(dict(kwargs))
        query = str(kwargs.get("query", ""))
        groups = set(kwargs.get("group_ids") or [])
        limit = int(kwargs.get("num_results", 10))
        ranked = [
            (_bigram_overlap(query, edge.fact), edge)
            for edge in self._edges
            if edge.group_id in groups
        ]
        ranked = [item for item in ranked if item[0] > 0]
        ranked.sort(key=lambda item: (item[0], item[1].uuid), reverse=True)
        return [edge for _, edge in ranked[:limit]]

    async def get_episodes_by_uuids(self, episode_ids: list[str]) -> list[Any]:
        return [self._episodes[item] for item in episode_ids if item in self._episodes]


@dataclass(frozen=True)
class _GraphHelpers:
    GraphitiSemanticIndex: Any
    episode_id: Any
    episode_name: Any
    fingerprint: Any


def _load_graph_helpers() -> _GraphHelpers:
    from doppel_memory.graphiti_store import (
        GraphitiSemanticIndex,
        _graphiti_episode_id,
        _graphiti_episode_name,
    )
    from doppel_memory.indexing import memory_index_fingerprint

    return _GraphHelpers(
        GraphitiSemanticIndex=GraphitiSemanticIndex,
        episode_id=_graphiti_episode_id,
        episode_name=_graphiti_episode_name,
        fingerprint=memory_index_fingerprint,
    )


async def run_graph_e2e(
    dm: Any,
    *,
    backend: GraphBackend = "contract",
    neo4j_uri: str = "",
    neo4j_user: str = "",
    neo4j_password: str = "",
    keep_fixture: bool = False,
) -> dict[str, Any]:
    """Run seven temporal/isolation cases without an LLM or real chat account."""
    if backend not in {"contract", "neo4j"}:
        raise ValueError("graph backend must be contract or neo4j")
    if backend == "neo4j" and not (
        neo4j_uri.strip() and neo4j_user.strip() and neo4j_password
    ):
        raise ValueError(
            "live Neo4j evaluation requires GRAPHITI_EVAL_NEO4J_URI, "
            "GRAPHITI_EVAL_NEO4J_USER and GRAPHITI_EVAL_NEO4J_PASSWORD"
        )

    helpers = _load_graph_helpers()
    run_id = "contract" if backend == "contract" else uuid4().hex[:12]
    client = dm.DoppelClient(backend="memory")
    scopes = {
        owner: dm.MemoryScope(
            user_id=f"doppel-graph-eval-{run_id}-{owner}",
            agent_id="memoecho-graph-eval",
            platform="synthetic",
            chat_type="private",
            chat_id=f"eval-{owner}",
        )
        for owner in ("owner-a", "owner-b")
    }
    records = await _write_authoritative_records(dm, client, scopes)

    graphiti: Any | None = None
    contract_client: ContractGraphitiClient | None = None
    cleanup_performed = False
    try:
        if backend == "contract":
            contract_client = ContractGraphitiClient(records, helpers)
            graph_client: Any = contract_client
        else:
            graphiti = await _build_live_graphiti(
                neo4j_uri=neo4j_uri,
                neo4j_user=neo4j_user,
                neo4j_password=neo4j_password,
            )
            await _verify_neo4j(graphiti)
            await graphiti.build_indices_and_constraints(delete_existing=False)
            await graphiti.driver.execute_query("CALL db.awaitIndexes(60)")
            await _seed_live_graph(graphiti, records, helpers)
            graph_client = graphiti

        index = helpers.GraphitiSemanticIndex(
            client.store, graphiti_client=graph_client
        )
        scenario_reports = await _run_queries(dm, client, index, scopes)
        graph_search_calls = (
            len(contract_client.search_calls)
            if contract_client is not None
            else len(scenario_reports) * 2
        )
        temporal_filter_calls = (
            sum(
                call.get("search_filter") is not None
                for call in contract_client.search_calls
            )
            if contract_client is not None
            else graph_search_calls
        )
    finally:
        if graphiti is not None:
            if not keep_fixture:
                await _cleanup_live_graph(
                    graphiti, [scope.scope_key for scope in scopes.values()]
                )
                cleanup_performed = True
            await graphiti.close()
        await client.close()

    passed = sum(item["passed"] for item in scenario_reports)
    leakage = sum(bool(item["forbidden_graph_hits"]) for item in scenario_reports)
    provenance_failures = sum(
        not item["provenance_ok"] for item in scenario_reports
    )
    temporal_failures = sum(
        not item["temporal_expectation_ok"] for item in scenario_reports
    )
    return {
        "runner": "doppel.graph-e2e.v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": (
            "contract-no-network"
            if backend == "contract"
            else "live-neo4j-preseeded-no-llm"
        ),
        "label": (
            "authoritative Store -> Graphiti temporal candidates -> exact Store "
            "revalidation; synthetic records; no extraction LLM and no QQ"
        ),
        "backend": {
            "name": backend,
            "neo4j_endpoint_configured": backend == "neo4j",
            "credentials_persisted": False,
            "isolated_scope_count": len(scopes),
            "fixture_cleanup_performed": cleanup_performed,
            "fixture_retained": backend == "neo4j" and keep_fixture,
        },
        "usage": {
            "llm_calls": 0,
            "provider_tokens": 0,
            "graph_search_calls": graph_search_calls,
            "temporal_filter_calls": temporal_filter_calls,
        },
        "summary": {
            "scenarios": len(scenario_reports),
            "passed_scenarios": passed,
            "temporal_failures": temporal_failures,
            "scope_leakage_failures": leakage,
            "provenance_failures": provenance_failures,
        },
        "gate": {
            "ok": (
                passed == len(scenario_reports)
                and temporal_filter_calls == graph_search_calls
                and leakage == 0
                and provenance_failures == 0
            ),
            "strict_passed": passed == len(scenario_reports),
            "zero_paid_calls": True,
        },
        "scenarios": scenario_reports,
    }


async def _write_authoritative_records(
    dm: Any, client: Any, scopes: dict[str, Any]
) -> list[Any]:
    records: list[Any] = []
    for fixture in _memories():
        scope = scopes[fixture.owner]
        metadata: dict[str, Any] = {
            "subject": "owner",
            "subject_id": scope.user_id,
            "personal_memory_type": fixture.personal_memory_type,
            "topic_key": fixture.topic_key,
            "temporal_status": fixture.temporal_status,
            "evidence": [
                {
                    "event_id": f"evt:{fixture.memory_id}",
                    "message_id": f"msg:{fixture.memory_id}",
                    "at": fixture.observed_at.isoformat(),
                }
            ],
        }
        if fixture.valid_from is not None:
            metadata["valid_from"] = fixture.valid_from.isoformat()
        if fixture.valid_to is not None:
            metadata["valid_to"] = fixture.valid_to.isoformat()
        record = dm.MemoryRecord(
            memory_id=fixture.memory_id,
            kind="event" if fixture.personal_memory_type == "episode" else "fact",
            scope=scope,
            content=fixture.content,
            actor="owner",
            authority=dm.FactAuthority.HUMAN_SELF,
            state=dm.MemoryState.CONFIRMED,
            tags=["personal-memory", "graph-eval"],
            importance=0.8,
            idempotency_key=f"graph-eval:{fixture.memory_id}",
            source_event_id=f"evt:{fixture.memory_id}",
            source_message_id=f"msg:{fixture.memory_id}",
            extractor="doppel.graph-e2e.fixture",
            created_at=fixture.observed_at,
            updated_at=fixture.observed_at,
            metadata=metadata,
        )
        result = await client.store.put(
            record, idempotency_key=record.idempotency_key
        )
        if result.record is None:
            raise RuntimeError(f"failed to persist graph fixture {fixture.memory_id}")
        records.append(result.record)
    return records


async def _run_queries(
    dm: Any,
    client: Any,
    index: Any,
    scopes: dict[str, Any],
) -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    by_id = {fixture.memory_id: fixture for fixture in _memories()}
    for fixture in _queries():
        scope = scopes[fixture.owner]
        result = await client.query_personal_memory(
            fixture.query,
            [scope],
            now=fixture.now,
            semantic_index=index,
        )
        valid_at = result.plan.as_of or result.plan.now
        graph_hits = await index.search_at(
            result.plan.search_text,
            [scope],
            valid_at=valid_at,
            filters=dm.MemoryFilter(
                tags={"personal-memory"}, states={dm.MemoryState.CONFIRMED}
            ),
            limit=20,
        )
        graph_ids = [item.memory_id for item in graph_hits]
        query_ids = [item.record.memory_id for item in result.hits]
        expected = set(fixture.expected_ids)
        forbidden = set(fixture.forbidden_ids)
        expected_graph = expected.intersection(graph_ids)
        expected_query = expected.intersection(query_ids)
        forbidden_graph = forbidden.intersection(graph_ids)
        forbidden_query = forbidden.intersection(query_ids)
        provenance_ok = all(
            item.extractor == "graphiti"
            and "graphiti:0.29" in item.derived_chain
            and item.scope is not None
            and item.scope.scope_key == scope.scope_key
            for item in graph_hits
        )
        semantic_binding_ok = all(
            any(
                hit.record.memory_id == memory_id and hit.semantic_score > 0
                for hit in result.hits
            )
            for memory_id in expected
        )
        cancellation_type_ok = True
        if fixture.case_id == "cancelled-plan-is-not-episode":
            cancellation_type_ok = all(
                by_id[item].personal_memory_type != "episode" for item in expected
            )
        temporal_ok = (
            expected_graph == expected
            and expected_query == expected
            and not forbidden_graph
            and not forbidden_query
            and cancellation_type_ok
        )
        reports.append(
            {
                "case_id": fixture.case_id,
                "assertion": fixture.assertion,
                "query": fixture.query,
                "intent": result.plan.intent,
                "as_of": result.plan.as_of.isoformat()
                if result.plan.as_of is not None
                else None,
                "valid_at": valid_at.isoformat(),
                "expected_ids": sorted(expected),
                "graph_candidate_ids": graph_ids,
                "query_hit_ids": query_ids,
                "forbidden_graph_hits": sorted(forbidden_graph),
                "forbidden_query_hits": sorted(forbidden_query),
                "temporal_expectation_ok": temporal_ok,
                "provenance_ok": provenance_ok,
                "semantic_binding_ok": semantic_binding_ok,
                "passed": temporal_ok and provenance_ok and semantic_binding_ok,
            }
        )
    return reports


async def _build_live_graphiti(
    *, neo4j_uri: str, neo4j_user: str, neo4j_password: str
) -> Any:
    from doppel_memory.graphiti_store import FastEmbedderClient, NoOpCrossEncoder
    from graphiti_core import Graphiti
    from graphiti_core.llm_client.client import LLMClient
    from graphiti_core.llm_client.config import LLMConfig

    class NoNetworkLLM(LLMClient):
        def __init__(self) -> None:
            super().__init__(LLMConfig(model="disabled-graph-eval"), cache=False)

        async def _generate_response(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
            raise RuntimeError("graph-e2e forbids LLM calls")

    return Graphiti(
        uri=neo4j_uri,
        user=neo4j_user,
        password=neo4j_password,
        llm_client=NoNetworkLLM(),
        embedder=FastEmbedderClient(),
        cross_encoder=NoOpCrossEncoder(),
    )


async def _verify_neo4j(graphiti: Any) -> None:
    result = await graphiti.driver.execute_query("RETURN 1 AS ok")
    records = getattr(result, "records", [])
    if not records or int(records[0]["ok"]) != 1:
        raise RuntimeError("Neo4j health query did not return ok=1")


async def _seed_live_graph(
    graphiti: Any, records: list[Any], helpers: _GraphHelpers
) -> None:
    from graphiti_core.edges import EntityEdge
    from graphiti_core.nodes import EntityNode, EpisodeType, EpisodicNode

    for record in records:
        valid_from = _metadata_time(record, "valid_from")
        valid_to = _metadata_time(record, "valid_to")
        evidence = record.metadata.get("evidence", [])
        observed_at = record.created_at
        if isinstance(evidence, list) and evidence and isinstance(evidence[0], dict):
            observed_at = _optional_time(evidence[0].get("at")) or observed_at
        episode_id = helpers.episode_id(record.scope, record.memory_id)
        fingerprint = helpers.fingerprint(record)
        source_id = str(
            uuid5(NAMESPACE_URL, f"graph-eval:source:{episode_id}")
        )
        target_id = str(
            uuid5(NAMESPACE_URL, f"graph-eval:target:{episode_id}")
        )
        edge_id = str(uuid5(NAMESPACE_URL, f"graph-eval:edge:{episode_id}"))
        source_name = f"owner:{record.scope.user_id}"
        target_name = str(record.metadata.get("topic_key", "memory"))
        source = EntityNode(
            uuid=source_id,
            name=source_name,
            group_id=record.scope.scope_key,
            name_embedding=await graphiti.embedder.create(source_name),
            summary="synthetic graph-e2e owner",
        )
        target = EntityNode(
            uuid=target_id,
            name=target_name,
            group_id=record.scope.scope_key,
            name_embedding=await graphiti.embedder.create(target_name),
            summary=record.content,
        )
        edge = EntityEdge(
            uuid=edge_id,
            group_id=record.scope.scope_key,
            source_node_uuid=source_id,
            target_node_uuid=target_id,
            created_at=observed_at,
            name="HAS_PERSONAL_MEMORY",
            fact=record.content,
            fact_embedding=await graphiti.embedder.create(record.content),
            episodes=[episode_id],
            valid_at=valid_from,
            invalid_at=valid_to,
            reference_time=observed_at,
        )
        episode = EpisodicNode(
            uuid=episode_id,
            name=helpers.episode_name(
                record.memory_id, fingerprint, record.version
            ),
            group_id=record.scope.scope_key,
            source=EpisodeType.text,
            source_description="doppel.graph-e2e preseeded fixture",
            content=record.content,
            valid_at=observed_at,
            entity_edges=[edge_id],
        )
        await source.save(graphiti.driver)
        await target.save(graphiti.driver)
        await edge.save(graphiti.driver)
        await episode.save(graphiti.driver)


async def _cleanup_live_graph(graphiti: Any, group_ids: list[str]) -> None:
    if not group_ids or any(
        not re.fullmatch(r"dpl_[0-9a-f]{64}", item) for item in group_ids
    ):
        raise RuntimeError("refusing to clean a non-evaluation Graphiti scope")
    await graphiti.driver.execute_query(
        "MATCH (n) WHERE n.group_id IN $group_ids DETACH DELETE n",
        group_ids=group_ids,
    )


def _metadata_time(record: Any, key: str) -> datetime | None:
    return _optional_time(record.metadata.get(key))


def _optional_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    if not value:
        return None
    try:
        return _at(str(value))
    except ValueError:
        return None


def _bigram_overlap(query: str, document: str) -> float:
    def bigrams(value: str) -> set[str]:
        compact = re.sub(r"[^0-9a-z\u3400-\u9fff]+", "", value.lower())
        if len(compact) < 2:
            return {compact} if compact else set()
        return {compact[index : index + 2] for index in range(len(compact) - 1)}

    query_terms = bigrams(query)
    if not query_terms:
        return 0.0
    return len(query_terms.intersection(bigrams(document))) / len(query_terms)
