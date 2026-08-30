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
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from doppel_eval.graph_e2e import (
    _cleanup_live_graph,
    _load_graph_helpers,
    _queries,
    _run_queries,
    _write_authoritative_records,
)
from doppel_eval.graphiti_provider import (
    BudgetedCachedGraphitiLLMClient,
    GraphitiProviderBudget,
)
from doppel_eval.graphiti_smoke import _preflight_neo4j
from doppel_eval.provider import (
    BudgetedCachedStructuredOutputModel,
    ProviderBudget,
    ProviderUsageLedger,
)

EVOLUTION_CONFIRM_ENV = "GRAPHITI_LIVE_EVOLUTION_CONFIRM"
EVOLUTION_CACHE_VERSION = "doppel.graphiti-evolution-cache.v1"
EVOLUTION_BUDGET = GraphitiProviderBudget(
    max_calls=60,
    max_input_tokens=600_000,
    max_output_tokens=61_440,
    max_total_tokens=700_000,
    max_output_tokens_per_call=1_024,
)
ANSWER_BUDGET = ProviderBudget(
    max_calls=10,
    max_input_tokens=50_000,
    max_output_tokens=5_120,
    max_total_tokens=60_000,
    max_output_tokens_per_call=512,
)
ANSWER_TOP_K = 3
ANSWER_INSTRUCTIONS = """\
Answer the user's personal-memory question using only the supplied evidence records.
Evidence is already restricted to authorized scopes, but it may contain semantically
irrelevant records. Respect the supplied current/as-of time and do not turn cancelled
plans into completed events. If the evidence does not directly answer the question,
set abstained=true, cite no memories, and say that the available memory is insufficient.
Otherwise cite every memory used, cite only supplied memory IDs, and keep the answer
concise. Never use outside knowledge or infer an unknown personal attribute.
"""


class EvidenceAnswer(BaseModel):
    """Host-level answer draft; Doppel itself continues to return evidence only."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    answer: str
    cited_memory_ids: list[str] = Field(default_factory=list)
    abstained: bool = False
    uncertainty: str = ""

    @field_validator("answer", "uncertainty", mode="before")
    @classmethod
    def _text(cls, value: Any) -> str:
        return str(value or "").strip()

    @field_validator("cited_memory_ids", mode="before")
    @classmethod
    def _citations(cls, value: Any) -> list[str]:
        if isinstance(value, str):
            value = [value]
        return list(
            dict.fromkeys(str(item or "").strip() for item in list(value or []))
        )

    @model_validator(mode="after")
    def _abstention_contract(self) -> EvidenceAnswer:
        if self.abstained and self.cited_memory_ids:
            raise ValueError("an abstention cannot cite memories")
        if not self.abstained and not self.answer:
            raise ValueError("a non-abstaining answer requires text")
        if any(not item for item in self.cited_memory_ids):
            raise ValueError("citation IDs must not be empty")
        return self


@dataclass(frozen=True)
class AnswerExpectation:
    case_id: str
    owner: str
    query: str
    now: datetime
    expected_memory_ids: tuple[str, ...]
    required_answer_terms: tuple[tuple[str, ...], ...] = ()
    forbidden_answer_assertions: tuple[str, ...] = ()
    should_abstain: bool = False


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
    evaluate_answers: bool = False,
    answer_cache_dir: Path | None = None,
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
    ranking: dict[str, Any] = {}
    answer_evaluation: dict[str, Any] = {"enabled": False}
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
            ranking = _ranking_metrics(scenarios)
            if evaluate_answers:
                answer_evaluation = await _run_answer_evaluation(
                    dm,
                    client=client,
                    index=index,
                    scopes=scopes,
                    model=model,
                    base_url=base_url,
                    api_key=api_key,
                    cache_dir=answer_cache_dir,
                )
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
        answer_evaluation=answer_evaluation,
        evaluate_answers=evaluate_answers,
    )
    return {
        "runner": "doppel.graphiti-evolution.v2",
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
        "ranking": ranking,
        "answer_evaluation": answer_evaluation,
        "summary": {
            "records": len(records),
            "indexed_records": len(indexed),
            "scenarios": len(scenarios),
            "passed_scenarios": passed,
            "answer_scenarios": int(
                (answer_evaluation.get("summary") or {}).get("scenarios", 0)
            ),
            "passed_answer_scenarios": int(
                (answer_evaluation.get("summary") or {}).get(
                    "passed_scenarios", 0
                )
            ),
        },
        "gate": gate,
        "scenarios": scenarios,
    }


def _answer_expectations() -> list[AnswerExpectation]:
    fixtures = {item.case_id: item for item in _queries()}

    def known(
        case_id: str,
        required_terms: tuple[tuple[str, ...], ...],
        *,
        forbidden_assertions: tuple[str, ...] = (),
    ) -> AnswerExpectation:
        fixture = fixtures[case_id]
        return AnswerExpectation(
            case_id=case_id,
            owner=fixture.owner,
            query=fixture.query,
            now=fixture.now,
            expected_memory_ids=fixture.expected_ids,
            required_answer_terms=required_terms,
            forbidden_answer_assertions=forbidden_assertions,
        )

    return [
        known(
            "current-after-temporary-residence",
            (("上海",),),
            forbidden_assertions=("现在住在北京", "长期住址是北京"),
        ),
        known("as-of-during-temporary-residence", (("北京",),)),
        known("late-arriving-historical-fact", (("深圳", "深圳项目组"),)),
        known(
            "cancelled-plan-is-not-episode",
            (("广州",), ("取消", "未成行", "没有成行")),
        ),
        known("correction-preserves-old-time-value", (("设计师",),)),
        known("correction-invalidates-old-value", (("产品经理",),)),
        known("cross-owner-temporal-isolation", (("上海",),)),
        AnswerExpectation(
            case_id="unknown-owner-blood-type-abstains",
            owner="owner-a",
            query="我的血型是什么？",
            now=datetime(2026, 6, 15, 12, tzinfo=UTC),
            expected_memory_ids=(),
            should_abstain=True,
        ),
    ]


def _ranking_metrics(scenarios: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "graph_candidates": _ranked_id_metrics(
            scenarios, id_field="graph_candidate_ids"
        ),
        "query_hits": _ranked_id_metrics(scenarios, id_field="query_hit_ids"),
    }


def _ranked_id_metrics(
    scenarios: list[dict[str, Any]], *, id_field: str
) -> dict[str, Any]:
    ks = (1, 3, 5)
    precision: dict[int, list[float]] = {value: [] for value in ks}
    recall: dict[int, list[float]] = {value: [] for value in ks}
    reciprocal_ranks: list[float] = []
    forbidden_hits = 0
    returned = 0
    for scenario in scenarios:
        ranked = [str(item) for item in scenario.get(id_field, [])]
        expected = {str(item) for item in scenario.get("expected_ids", [])}
        forbidden = {
            str(item)
            for item in (
                scenario.get("forbidden_graph_hits", [])
                if id_field == "graph_candidate_ids"
                else scenario.get("forbidden_query_hits", [])
            )
        }
        if not expected:
            continue
        returned += len(ranked)
        forbidden_hits += len(forbidden)
        first_rank = next(
            (index for index, item in enumerate(ranked, start=1) if item in expected),
            0,
        )
        reciprocal_ranks.append(1.0 / first_rank if first_rank else 0.0)
        for value in ks:
            relevant = len(expected.intersection(ranked[:value]))
            precision[value].append(relevant / value)
            recall[value].append(relevant / len(expected))
    count = len(reciprocal_ranks)
    return {
        "queries": count,
        "mean_returned_candidates": round(returned / count, 6) if count else 0.0,
        "top1_accuracy": round(
            sum(value == 1.0 for value in reciprocal_ranks) / count, 6
        )
        if count
        else 0.0,
        "mean_reciprocal_rank": round(sum(reciprocal_ranks) / count, 6)
        if count
        else 0.0,
        "forbidden_hits": forbidden_hits,
        "precision_at": {
            str(value): round(sum(precision[value]) / count, 6) if count else 0.0
            for value in ks
        },
        "recall_at": {
            str(value): round(sum(recall[value]) / count, 6) if count else 0.0
            for value in ks
        },
    }


async def _run_answer_evaluation(
    dm: Any,
    *,
    client: Any,
    index: Any,
    scopes: dict[str, Any],
    model: str,
    base_url: str,
    api_key: str,
    cache_dir: Path | None,
) -> dict[str, Any]:
    ledger = ProviderUsageLedger(ANSWER_BUDGET)
    provider = dm.OpenAICompatibleStructuredOutputModel(
        dm.OpenAICompatibleStructuredOutputConfig(
            model=model,
            base_url=base_url,
            schema_mode="json_object",
            max_completion_tokens=ANSWER_BUDGET.max_output_tokens_per_call,
            max_tokens_parameter="max_tokens",
            temperature=0,
            thinking="disabled",
        ),
        api_key=api_key,
        usage_observer=ledger.observe_usage,
    )
    cached = BudgetedCachedStructuredOutputModel(
        provider, ledger, cache_dir=cache_dir
    )
    reports: list[dict[str, Any]] = []
    hard_failure = ""
    try:
        for expectation in _answer_expectations():
            result = await client.query_personal_memory(
                expectation.query,
                [scopes[expectation.owner]],
                now=expectation.now,
                semantic_index=index,
            )
            evidence = result.hits[:ANSWER_TOP_K]
            supplied_ids = [item.record.memory_id for item in evidence]
            request = dm.StructuredGenerationRequest(
                instructions=ANSWER_INSTRUCTIONS,
                input={
                    "query": expectation.query,
                    "intent": result.plan.intent,
                    "now": result.plan.now.isoformat(),
                    "as_of": (
                        result.plan.as_of.isoformat()
                        if result.plan.as_of is not None
                        else None
                    ),
                    "ambiguous": result.ambiguous,
                    "evidence": [
                        {
                            "memory_id": item.record.memory_id,
                            "content": item.record.content,
                            "personal_memory_type": item.record.metadata.get(
                                "personal_memory_type", ""
                            ),
                            "temporal_status": item.record.metadata.get(
                                "temporal_status", ""
                            ),
                            "valid_from": item.record.metadata.get(
                                "valid_from"
                            ),
                            "valid_to": item.record.metadata.get("valid_to"),
                            "score": item.score,
                        }
                        for item in evidence
                    ],
                },
                output_schema=EvidenceAnswer.model_json_schema(),
            )
            try:
                answer = EvidenceAnswer.model_validate(await cached.generate(request))
                score = _score_answer(
                    expectation,
                    answer,
                    supplied_ids=supplied_ids,
                )
                reports.append(
                    {
                        "case_id": expectation.case_id,
                        "query": expectation.query,
                        "expected_memory_ids": list(
                            expectation.expected_memory_ids
                        ),
                        "supplied_memory_ids": supplied_ids,
                        "answer": answer.answer,
                        "cited_memory_ids": answer.cited_memory_ids,
                        "abstained": answer.abstained,
                        "uncertainty": answer.uncertainty,
                        **score,
                    }
                )
            except Exception as exc:  # noqa: BLE001 - provider boundary in report
                hard_failure = f"{expectation.case_id}: {type(exc).__name__}: {exc}"
                reports.append(
                    {
                        "case_id": expectation.case_id,
                        "query": expectation.query,
                        "expected_memory_ids": list(
                            expectation.expected_memory_ids
                        ),
                        "supplied_memory_ids": supplied_ids,
                        "error": hard_failure,
                        "passed": False,
                    }
                )
                break
    finally:
        await provider.aclose()

    usage = ledger.report()
    passed = sum(bool(item.get("passed")) for item in reports)
    expected_count = len(_answer_expectations())
    unknown = next(
        (
            item
            for item in reports
            if item.get("case_id") == "unknown-owner-blood-type-abstains"
        ),
        {},
    )
    checks = {
        "no_hard_failure": not hard_failure,
        "all_scenarios_completed": len(reports) == expected_count,
        "all_answers_passed": passed == expected_count,
        "unknown_attribute_abstained": bool(unknown.get("abstained")),
        "unknown_attribute_has_no_citations": not bool(
            unknown.get("cited_memory_ids")
        ),
        "within_budget": bool(usage.get("within_budget")),
    }
    return {
        "enabled": True,
        "mode": "host-level-evidence-only-structured-answer",
        "top_k": ANSWER_TOP_K,
        "provider": {
            "model": model,
            "base_url_host": _host_only(base_url),
            "key_persisted": False,
        },
        "usage": usage,
        "hard_failure": hard_failure,
        "summary": {
            "scenarios": expected_count,
            "completed_scenarios": len(reports),
            "passed_scenarios": passed,
            "abstention_scenarios": 1,
        },
        "gate": {"ok": all(checks.values()), "checks": checks},
        "scenarios": reports,
    }


def _score_answer(
    expectation: AnswerExpectation,
    answer: EvidenceAnswer,
    *,
    supplied_ids: list[str],
) -> dict[str, Any]:
    supplied = set(supplied_ids)
    citations = set(answer.cited_memory_ids)
    expected = set(expectation.expected_memory_ids)
    citation_subset_ok = citations.issubset(supplied)
    expected_citations_ok = expected.issubset(citations)
    normalized = _normalize_answer_text(answer.answer)
    missing_term_groups = [
        list(group)
        for group in expectation.required_answer_terms
        if not any(_normalize_answer_text(term) in normalized for term in group)
    ]
    forbidden_assertions = [
        assertion
        for assertion in expectation.forbidden_answer_assertions
        if _normalize_answer_text(assertion) in normalized
    ]
    if expectation.should_abstain:
        passed = answer.abstained and not citations
    else:
        passed = (
            not answer.abstained
            and citation_subset_ok
            and expected_citations_ok
            and not missing_term_groups
            and not forbidden_assertions
        )
    return {
        "citation_subset_ok": citation_subset_ok,
        "expected_citations_ok": expected_citations_ok,
        "missing_answer_term_groups": missing_term_groups,
        "forbidden_answer_assertions": forbidden_assertions,
        "passed": passed,
    }


def _normalize_answer_text(value: str) -> str:
    return "".join(character.lower() for character in value if character.isalnum())


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
    answer_evaluation: dict[str, Any],
    evaluate_answers: bool,
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
        "answer_evaluation_passed": (
            not evaluate_answers
            or bool((answer_evaluation.get("gate") or {}).get("ok"))
        ),
    }
    return {"ok": all(checks.values()), "checks": checks}


def _host_only(base_url: str) -> str:
    from urllib.parse import urlsplit

    parsed = urlsplit(str(base_url or "").strip())
    return parsed.netloc or ""
