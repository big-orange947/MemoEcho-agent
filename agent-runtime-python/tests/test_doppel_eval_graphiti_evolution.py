"""Offline guards and strict-gate tests for the paid evolution runner."""

from __future__ import annotations

import os
import unittest
from types import SimpleNamespace

from doppel_eval.graphiti_evolution import (
    EVOLUTION_CONFIRM_ENV,
    _evolution_gate,
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
        )
        self.assertFalse(failed["ok"])
        self.assertFalse(failed["checks"]["raw_subject_ids_absent"])


if __name__ == "__main__":
    unittest.main()
