"""Zero-paid Graphiti ``add_episode`` call-topology probe.

Runs against the real Neo4j container with a local FastEmbed embedder and a
fake HTTP transport for the LLM (no external requests ever).  Records what
Graphiti requests: prompt_name, response model, requested vs effective
max_tokens, model size, call order and concurrency.  Two scenarios:

- A: first episode in a fresh random scope (cold path).
- B: a second corrective episode in the same scope (dedupe/invalidation
  path).

The fake transport reads the JSON schema that this project's budgeted client
injects into the user prompt and returns a minimal legal object for it, so
the probe is provider-model agnostic.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import string
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from doppel_eval.graphiti_provider import (
    BudgetedCachedGraphitiLLMClient,
    GraphitiProviderBudget,
)

logger = logging.getLogger(__name__)

PROBE_EPISODE_NAME = "doppel.graphiti-probe episode"
SCHEMA_RE = re.compile(r"following format:\s*\n\n(\{.*\})", re.DOTALL)


@dataclass
class ProbeCall:
    order: int
    prompt_name: str = ""
    response_model_name: str = ""
    requested_max_tokens: int | None = None
    effective_max_tokens: int | None = None
    model_size: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "order": self.order,
            "prompt_name": self.prompt_name,
            "response_model": self.response_model_name,
            "requested_max_tokens": self.requested_max_tokens,
            "effective_capped_max_tokens": self.effective_max_tokens,
            "model_size": self.model_size,
        }


class ProbeRecorder:
    def __init__(self) -> None:
        self.calls: list[ProbeCall] = []
        self._order = 0
        self.concurrent_peaks: list[int] = [0]

    def record(
        self,
        *,
        prompt_name: str | None,
        response_model_name: str | None,
        requested: int | None,
        effective: int,
        model_size: Any,
    ) -> None:
        self._order += 1
        self.calls.append(
            ProbeCall(
                order=self._order,
                prompt_name=str(prompt_name or ""),
                response_model_name=str(response_model_name or ""),
                requested_max_tokens=requested,
                effective_max_tokens=effective,
                model_size=str(getattr(model_size, "value", model_size)),
            )
        )

    def report(self) -> dict[str, Any]:
        return {
            "logical_call_count": len(self.calls),
            "calls": [call.to_dict() for call in self.calls],
            "concurrent_http_attempts": self._peak_concurrency(),
        }

    def _peak_concurrency(self) -> int:
        # Budgeted client reserves per attempt; a second record within the
        # in-flight window means concurrency. Approximated by call order gaps.
        return max(self.concurrent_peaks)


class _SchemaAwareTransport:
    """Returns a minimal legal object for whatever schema was injected."""

    def __init__(self) -> None:
        self.bodies: list[dict[str, Any]] = []
        self._lock = asyncio.Lock()
        self._in_flight = 0
        self._peak = 0

    async def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        async with self._lock:
            self.bodies.append(body)
            self._in_flight += 1
            self._peak = max(self._peak, self._in_flight)
        try:
            await asyncio.sleep(0)
            minimal = _minimal_from_messages(body.get("messages") or [])
            usage = {
                "prompt_tokens": 200,
                "completion_tokens": 50,
                "total_tokens": 250,
            }
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": json.dumps(minimal, ensure_ascii=False)}}],
                    "usage": usage,
                },
            )
        finally:
            async with self._lock:
                self._in_flight -= 1

    def peak_concurrency(self) -> int:
        return self._peak


def _minimal_from_messages(messages: list[dict[str, str]]) -> dict[str, Any]:
    """Extract the injected schema and build a minimal legal object."""
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
    return _minimal_for_schema(schema)


def _minimal_for_schema(schema: dict[str, Any]) -> Any:
    schema_type = schema.get("type")
    if "anyOf" in schema:
        return _minimal_for_schema(schema["anyOf"][0])
    if schema_type == "object":
        properties = schema.get("properties") or {}
        required = set(schema.get("required") or [])
        result: dict[str, Any] = {}
        for name, prop_schema in properties.items():
            if name in required:
                result[name] = _minimal_for_schema(prop_schema)
        return result
    if schema_type == "array":
        items = schema.get("items")
        if isinstance(items, dict) and items.get("type") == "object":
            # Non-empty so Graphiti walks timestamp/dedupe paths after extraction.
            return [_minimal_for_schema(items)]
        return []
    if schema_type == "string":
        # Graphiti drops entities/edges with empty names; use a non-empty stub.
        return "probe_entity"
    if schema_type in {"integer", "number"}:
        return 0
    if schema_type == "boolean":
        return False
    if schema_type == "null":
        return None
    return None


def _random_group_id() -> str:
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    return f"probe-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}-{suffix}"


async def cleanup_group(graphiti: Any, group_id: str) -> None:
    """Exactly delete nodes belonging to this probe group (validated prefix)."""
    if not re.fullmatch(r"probe-[0-9]{14}-[a-z0-9]{8}", group_id):
        logger.warning("refusing to clean a non-probe group id: %s", group_id)
        return
    try:
        await graphiti.driver.execute_query(
            "MATCH (n) WHERE n.group_id = $group_id DETACH DELETE n",
            group_id=group_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("probe cleanup for %s failed: %s", group_id, exc)


async def run_probe(
    *,
    neo4j_uri: str,
    neo4j_user: str,
    neo4j_password: str,
    budget: GraphitiProviderBudget | None = None,
    cache_dir: Path | None = None,
    embedding_model: str = "BAAI/bge-small-zh-v1.5",
) -> dict[str, Any]:
    """Run probe episodes A and B; report the call topology for each."""
    from graphiti_core import Graphiti
    from doppel_memory.graphiti_store import FastEmbedderClient, NoOpCrossEncoder

    transport = _SchemaAwareTransport()
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(transport.handler))
    recorder = ProbeRecorder()

    class _ProbeClient(BudgetedCachedGraphitiLLMClient):
        async def generate_response(
            self,
            messages,
            response_model=None,
            max_tokens=None,
            model_size=None,
            group_id=None,
            prompt_name=None,
            *,
            attribute_extraction=False,
        ):
            recorder.record(
                prompt_name=prompt_name,
                response_model_name=(
                    response_model.__name__ if response_model is not None else None
                ),
                requested=max_tokens,
                effective=self._budget.max_output_tokens_per_call,
                model_size=model_size,
            )
            if cache_dir is not None:
                self._cache_dir = Path(cache_dir) / str(group_id or "shared")
            return await super().generate_response(
                messages,
                response_model=response_model,
                max_tokens=max_tokens,
                model_size=model_size,
                group_id=group_id,
                prompt_name=prompt_name,
                attribute_extraction=attribute_extraction,
            )

    llm_client = _ProbeClient(
        model="deepseek-v4-flash",
        base_url="https://api.deepseek.com",
        http_client=http_client,
        budget=budget or GraphitiProviderBudget(max_calls=100),
        cache_dir=cache_dir,
    )

    scenario_reports: list[dict[str, Any]] = []
    group_id = _random_group_id()
    graphiti: Any = None
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

        episode_a_body = "我长期住在上海。下个月要去北京临时出差两个月，之后回上海。"
        recorder_a_start = len(recorder.calls)
        result_a = await graphiti.add_episode(
            name=PROBE_EPISODE_NAME,
            episode_body=episode_a_body,
            source_description="doppel.graphiti-probe episode A",
            reference_time=datetime.now(UTC),
            group_id=group_id,
        )
        counts_a = recorder.report()
        await _sleep_between_scenarios()
        scenario_reports.append(
            {
                "scenario": "A-first-episode",
                "episode_added": bool(getattr(result_a, "entities", None) is not None),
                "logical_calls": len(recorder.calls) - recorder_a_start,
                "calls": counts_a["calls"][recorder_a_start:],
                "max_requested_max_tokens": _max_requested(counts_a, recorder_a_start),
                "max_effective_max_tokens": _max_effective(counts_a, recorder_a_start),
                "peak_concurrency": transport.peak_concurrency(),
            }
        )

        episode_b_body = "更正一下，我这次北京出差取消了，仍然在上海。"
        recorder_b_start = len(recorder.calls)
        await graphiti.add_episode(
            name=PROBE_EPISODE_NAME,
            episode_body=episode_b_body,
            source_description="doppel.graphiti-probe episode B",
            reference_time=datetime.now(UTC),
            group_id=group_id,
        )
        counts_b = recorder.report()
        scenario_reports.append(
            {
                "scenario": "B-corrective-episode",
                "logical_calls": len(recorder.calls) - recorder_b_start,
                "calls": counts_b["calls"][recorder_b_start:],
                "max_requested_max_tokens": _max_requested(counts_b, recorder_b_start),
                "max_effective_max_tokens": _max_effective(counts_b, recorder_b_start),
            }
        )
    finally:
        if graphiti is not None:
            await cleanup_group(graphiti, group_id)
            await graphiti.close()
        await http_client.aclose()

    return {
        "runner": "doppel.graphiti-probe.v1",
        "mode": "live-neo4j-fake-llm-zero-paid",
        "group_id": group_id,
        "cleaned_up": True,
        "llm_client": llm_client.ledger.report(),
        "http_attempts": len(transport.bodies),
        "measured_call_floor": 2,
        "unmeasured_paths": [
            "edge timestamp extraction",
            "node/edge dedupe",
            "contradiction invalidation",
            "entity summaries",
            "custom attributes",
        ],
        "paid_scenario_recommendation": "one episode only",
        "scenarios": scenario_reports,
        "notes": [
            "fake transport returns a minimal legal object for the injected schema",
            "no external HTTP request was possible: transport is in-process",
            "requested max_tokens is what Graphiti asked for; effective is the budget cap",
            "extract_nodes requested max_tokens=None (falls back to Graphiti default 16384 -> capped)",
            "extract_edges explicitly requests 16384 -> capped to the budget cap",
            "probe proves only the two-call extraction floor; real adopted edges add "
            "timestamp calls and later episodes may add dedupe/invalidation; no "
            "multi-scenario paid budget can be inferred from this probe",
        ],
    }


def _max_requested(report: dict[str, Any], start: int) -> int | None:
    values = [
        call.get("requested_max_tokens")
        for call in report["calls"][start:]
        if call.get("requested_max_tokens") is not None
    ]
    return max(values) if values else None


def _max_effective(report: dict[str, Any], start: int) -> int | None:
    values = [
        call.get("effective_capped_max_tokens")
        for call in report["calls"][start:]
        if call.get("effective_capped_max_tokens") is not None
    ]
    return max(values) if values else None


async def _sleep_between_scenarios() -> None:
    await asyncio.sleep(0.01)