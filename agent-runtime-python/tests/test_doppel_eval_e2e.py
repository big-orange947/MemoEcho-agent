from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from doppel_eval.e2e import run_e2e
from doppel_eval.provider import ProviderBudget
from doppel_eval.replay import _load_doppel
from doppel_eval.semantic import _cosine


class _NoMemoryProvider:
    name = "fake-no-memory-provider"
    version = "1"

    def __init__(self, config, *, api_key="", usage_observer=None, **kwargs) -> None:
        self.config = config
        self.usage_observer = usage_observer

    async def generate(self, request) -> dict:
        if self.usage_observer is not None:
            self.usage_observer(
                {
                    "input_tokens": 100,
                    "output_tokens": 5,
                    "total_tokens": 105,
                    "cached_input_tokens": 0,
                    "cache_miss_input_tokens": 100,
                    "reasoning_tokens": 0,
                }
            )
        return {"memories": []}

    async def aclose(self) -> None:
        return None


class _TwoTripProvider(_NoMemoryProvider):
    name = "fake-two-trip-provider"

    async def generate(self, request) -> dict:
        if self.usage_observer is not None:
            self.usage_observer(
                {
                    "input_tokens": 200,
                    "output_tokens": 100,
                    "total_tokens": 300,
                    "cached_input_tokens": 0,
                    "cache_miss_input_tokens": 200,
                    "reasoning_tokens": 0,
                }
            )
        owner_messages = [
            message
            for message in request.input["messages"]
            if message["actor"] == "owner"
        ]
        return {
            "memories": [
                {
                    "content": "去年国庆去杭州旅游五天。",
                    "memory_type": "episode",
                    "kind": "event",
                    "event_key": "trip:2025-10:hangzhou",
                    "temporal_status": "historical",
                    "subject": "owner",
                    "evidence_ids": [owner_messages[0]["evidence_id"]],
                },
                {
                    "content": "今年五月去成都旅行一周。",
                    "memory_type": "episode",
                    "kind": "event",
                    "event_key": "trip:2026-05:chengdu",
                    "temporal_status": "historical",
                    "subject": "owner",
                    "evidence_ids": [owner_messages[1]["evidence_id"]],
                },
            ]
        }


class _CancelledPlanProvider(_NoMemoryProvider):
    name = "fake-cancelled-plan-provider"

    async def generate(self, request) -> dict:
        if self.usage_observer is not None:
            self.usage_observer(
                {
                    "input_tokens": 150,
                    "output_tokens": 50,
                    "total_tokens": 200,
                    "cached_input_tokens": 0,
                    "cache_miss_input_tokens": 150,
                    "reasoning_tokens": 0,
                }
            )
        owner_messages = [
            message
            for message in request.input["messages"]
            if message["actor"] == "owner"
        ]
        return {
            "memories": [
                {
                    "content": "北京旅行计划已经取消，最终没有成行。",
                    "memory_type": "plan",
                    "kind": "fact",
                    "revision_kind": "retraction",
                    "topic_key": "travel.plan.beijing",
                    "temporal_status": "historical",
                    "subject": "owner",
                    "evidence_ids": [
                        owner_messages[0]["evidence_id"],
                        owner_messages[1]["evidence_id"],
                    ],
                }
            ]
        }


class _FailingProvider(_NoMemoryProvider):
    name = "fake-failing-provider"

    async def generate(self, request) -> dict:
        raise RuntimeError("synthetic provider failure")


class E2ERunnerTest(unittest.IsolatedAsyncioTestCase):
    async def test_provider_errors_are_hard_failures_not_quality_misses(self) -> None:
        if not os.environ.get("DOPPEL_IMPORT_PATH"):
            self.skipTest("DOPPEL_IMPORT_PATH not set")
        dm = _load_doppel()
        if dm is None:
            self.skipTest("doppel_memory not importable")
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            dm, "OpenAICompatibleStructuredOutputModel", _FailingProvider
        ):
            report = await run_e2e(
                dm,
                model="fake-model",
                base_url="https://models.invalid/v1",
                budget=ProviderBudget(max_calls=2),
                cache_dir=Path(tmp),
                case_ids=["noise-only"],
                max_scenes=1,
            )

        self.assertFalse(report["gate"]["ok"])
        self.assertFalse(report["scenarios"][0]["passed"])
        self.assertEqual(report["summary"]["provider_errors"], 2)
        self.assertEqual(report["summary"]["unexpected_processing_errors"], 2)
        errors = report["scenarios"][0]["processing"][0]["errors"]
        self.assertEqual(errors[0]["classification"], "unexpected")

    async def test_cancelled_plan_is_not_counted_as_completed_trip(self) -> None:
        if not os.environ.get("DOPPEL_IMPORT_PATH"):
            self.skipTest("DOPPEL_IMPORT_PATH not set")
        dm = _load_doppel()
        if dm is None:
            self.skipTest("doppel_memory not importable")
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            dm, "OpenAICompatibleStructuredOutputModel", _CancelledPlanProvider
        ):
            report = await run_e2e(
                dm,
                model="fake-model",
                base_url="https://models.invalid/v1",
                budget=ProviderBudget(max_calls=1),
                cache_dir=Path(tmp),
                case_ids=["travel-count-cancelled-plan"],
                max_scenes=1,
            )

        query = report["scenarios"][0]["queries"][0]
        self.assertEqual(query["count_status"], "exact")
        self.assertEqual(query["count_value"], 0)
        self.assertEqual(query["distinct_event_keys"], [])
        self.assertTrue(query["count_ok"])
        self.assertTrue(report["gate"]["strict_passed"])

    async def test_two_distinct_trips_produce_exact_count_without_gold(self) -> None:
        if not os.environ.get("DOPPEL_IMPORT_PATH"):
            self.skipTest("DOPPEL_IMPORT_PATH not set")
        dm = _load_doppel()
        if dm is None:
            self.skipTest("doppel_memory not importable")
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            dm, "OpenAICompatibleStructuredOutputModel", _TwoTripProvider
        ):
            report = await run_e2e(
                dm,
                model="fake-model",
                base_url="https://models.invalid/v1",
                budget=ProviderBudget(max_calls=1),
                cache_dir=Path(tmp),
                case_ids=["travel-count-two-distinct"],
                max_scenes=1,
            )

        query = report["scenarios"][0]["queries"][0]
        self.assertEqual(query["count_status"], "exact")
        self.assertEqual(query["count_value"], 2)
        self.assertEqual(len(query["distinct_event_keys"]), 2)
        self.assertTrue(query["count_ok"])
        self.assertTrue(report["gate"]["strict_passed"])

    async def test_noise_scene_runs_without_gold_memory_or_network(self) -> None:
        if not os.environ.get("DOPPEL_IMPORT_PATH"):
            self.skipTest("DOPPEL_IMPORT_PATH not set")
        dm = _load_doppel()
        if dm is None:
            self.skipTest("doppel_memory not importable")
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            dm, "OpenAICompatibleStructuredOutputModel", _NoMemoryProvider
        ):
            report = await run_e2e(
                dm,
                model="fake-model",
                base_url="https://models.invalid/v1",
                budget=ProviderBudget(max_calls=2),
                cache_dir=Path(tmp),
                case_ids=["noise-only"],
                max_scenes=1,
            )

        self.assertEqual(report["summary"]["provider_calls"], 2)
        self.assertEqual(report["usage"]["total_tokens"], 210)
        self.assertEqual(report["scenarios"][0]["processing"][0]["proposals"], 0)
        self.assertEqual(report["runner"], "doppel.e2e.v3")
        self.assertEqual(
            report["retrieval"],
            {"mode": "lexical", "embedding_model": "", "index": "none"},
        )
        self.assertTrue(report["gate"]["strict_passed"])


class EvaluationSemanticMathTest(unittest.TestCase):
    def test_cosine_is_domain_neutral_vector_math(self) -> None:
        self.assertEqual(_cosine([1.0, 0.0], [1.0, 0.0]), 1.0)
        self.assertEqual(_cosine([1.0, 0.0], [0.0, 1.0]), 0.0)
        self.assertEqual(_cosine([0.0, 0.0], [1.0, 1.0]), 0.0)

    def test_cosine_rejects_incompatible_vectors(self) -> None:
        with self.assertRaisesRegex(ValueError, "equal non-zero dimensions"):
            _cosine([1.0], [1.0, 2.0])


if __name__ == "__main__":
    unittest.main()
