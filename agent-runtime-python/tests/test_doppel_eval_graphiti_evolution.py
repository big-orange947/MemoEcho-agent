"""Offline guards and strict-gate tests for the paid evolution runner."""

from __future__ import annotations

import os
import unittest
from datetime import UTC, datetime
from types import SimpleNamespace

from doppel_eval.graphiti_evolution import (
    EVOLUTION_CONFIRM_ENV,
    AnswerExpectation,
    EvidenceAnswer,
    _evolution_gate,
    _ranking_metrics,
    _score_answer,
    validate_evolution_activation,
)


class EvolutionActivationTest(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop(EVOLUTION_CONFIRM_ENV, None)

    def test_confirmation_is_required(self) -> None:
        with self.assertRaisesRegex(ValueError, EVOLUTION_CONFIRM_ENV):
            validate_evolution_activation(
                model="model", base_url="https://example.invalid", api_key="key"
            )

    def test_complete_configuration_passes_after_confirmation(self) -> None:
        os.environ[EVOLUTION_CONFIRM_ENV] = "YES"
        validate_evolution_activation(
            model="model", base_url="https://example.invalid", api_key="key"
        )

    def test_missing_key_is_rejected(self) -> None:
        os.environ[EVOLUTION_CONFIRM_ENV] = "YES"
        with self.assertRaisesRegex(ValueError, "DOPPEL_API_KEY"):
            validate_evolution_activation(
                model="model", base_url="https://example.invalid", api_key=""
            )


class EvolutionGateTest(unittest.TestCase):
    def test_gate_requires_every_dimension(self) -> None:
        records = [SimpleNamespace(memory_id="m1"), SimpleNamespace(memory_id="m2")]
        indexed = [{"memory_id": "m1"}, {"memory_id": "m2"}]
        scenarios = [{"passed": True}, {"passed": True}]
        projection = {"edges_by_memory": {"m1": 1, "m2": 2}}
        privacy = {"raw_subject_leaks": 0, "pseudonymous_subjects": 2}
        usage = {"within_budget": True}

        gate = _evolution_gate(
            hard_failure="",
            indexed=indexed,
            records=records,
            scenarios=scenarios,
            projection=projection,
            privacy=privacy,
            cleanup_performed=True,
            keep_fixture=False,
            usage=usage,
            answer_evaluation={"enabled": False},
            evaluate_answers=False,
        )

        self.assertTrue(gate["ok"])
        privacy["raw_subject_leaks"] = 1
        failed = _evolution_gate(
            hard_failure="",
            indexed=indexed,
            records=records,
            scenarios=scenarios,
            projection=projection,
            privacy=privacy,
            cleanup_performed=True,
            keep_fixture=False,
            usage=usage,
            answer_evaluation={"enabled": False},
            evaluate_answers=False,
        )
        self.assertFalse(failed["ok"])
        self.assertFalse(failed["checks"]["raw_subject_ids_absent"])

    def test_enabled_answer_gate_must_pass(self) -> None:
        gate = _evolution_gate(
            hard_failure="",
            indexed=[{"memory_id": "m1"}],
            records=[SimpleNamespace(memory_id="m1")],
            scenarios=[{"passed": True}],
            projection={"edges_by_memory": {"m1": 1}},
            privacy={"raw_subject_leaks": 0, "pseudonymous_subjects": 2},
            cleanup_performed=True,
            keep_fixture=False,
            usage={"within_budget": True},
            answer_evaluation={"gate": {"ok": False}},
            evaluate_answers=True,
        )

        self.assertFalse(gate["ok"])
        self.assertFalse(gate["checks"]["answer_evaluation_passed"])


class RankingMetricsTest(unittest.TestCase):
    def test_metrics_keep_precision_recall_and_forbidden_hits_separate(self) -> None:
        metrics = _ranking_metrics(
            [
                {
                    "expected_ids": ["right"],
                    "graph_candidate_ids": ["noise", "right", "forbidden"],
                    "query_hit_ids": ["right", "noise"],
                    "forbidden_graph_hits": ["forbidden"],
                    "forbidden_query_hits": [],
                }
            ]
        )

        self.assertEqual(metrics["graph_candidates"]["top1_accuracy"], 0)
        self.assertEqual(metrics["graph_candidates"]["mean_reciprocal_rank"], 0.5)
        self.assertEqual(metrics["graph_candidates"]["forbidden_hits"], 1)
        self.assertEqual(metrics["query_hits"]["top1_accuracy"], 1)
        self.assertEqual(metrics["query_hits"]["recall_at"]["1"], 1)
        self.assertAlmostEqual(
            metrics["query_hits"]["precision_at"]["3"], 1 / 3, places=6
        )


class AnswerScoringTest(unittest.TestCase):
    def setUp(self) -> None:
        self.expectation = AnswerExpectation(
            case_id="known",
            owner="owner-a",
            query="where",
            now=datetime(2026, 1, 1, tzinfo=UTC),
            expected_memory_ids=("m1",),
            required_answer_terms=(("上海",),),
            forbidden_answer_assertions=("现在住在北京",),
        )

    def test_known_answer_requires_expected_evidence_and_content(self) -> None:
        scored = _score_answer(
            self.expectation,
            EvidenceAnswer(
                answer="目前长期住在上海。",
                cited_memory_ids=["m1"],
            ),
            supplied_ids=["m1", "noise"],
        )

        self.assertTrue(scored["passed"])

    def test_unknown_attribute_must_abstain_without_citations(self) -> None:
        expectation = AnswerExpectation(
            case_id="unknown",
            owner="owner-a",
            query="blood type",
            now=datetime(2026, 1, 1, tzinfo=UTC),
            expected_memory_ids=(),
            should_abstain=True,
        )

        scored = _score_answer(
            expectation,
            EvidenceAnswer(answer="记忆中没有相关信息。", abstained=True),
            supplied_ids=["irrelevant"],
        )

        self.assertTrue(scored["passed"])


if __name__ == "__main__":
    unittest.main()
