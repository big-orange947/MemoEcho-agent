"""Smoke runner tests: double-switch live protection + dry-run behavior."""

from __future__ import annotations

import os
import unittest

from doppel_eval.graphiti_smoke import (
    LIVE_CONFIRM_ENV,
    _validate_provider_activation,
    run_smoke,
)

NEO4J_URI = os.environ.get("GRAPHITI_EVAL_NEO4J_URI", "").strip()
NEO4J_USER = os.environ.get("GRAPHITI_EVAL_NEO4J_USER", "").strip()
NEO4J_PASSWORD = os.environ.get("GRAPHITI_EVAL_NEO4J_PASSWORD", "")


class LiveGuardTest(unittest.IsolatedAsyncioTestCase):
    """Live mode must require both explicit switches and full credentials."""

    async def test_live_without_confirm_is_rejected_before_network(self) -> None:
        os.environ.pop(LIVE_CONFIRM_ENV, None)
        with self.assertRaises(ValueError) as ctx:
            _validate_provider_activation(
                live_provider=True,
                model="m",
                base_url="https://api.deepseek.com",
                api_key="k",
            )
        self.assertIn(LIVE_CONFIRM_ENV, str(ctx.exception))

    async def test_live_with_confirm_missing_keys_is_rejected(self) -> None:
        os.environ[LIVE_CONFIRM_ENV] = "YES"
        try:
            with self.assertRaises(ValueError) as ctx:
                _validate_provider_activation(
                    live_provider=True,
                    model="",
                    base_url="",
                    api_key="",
                )
            message = str(ctx.exception)
            self.assertIn("DOPPEL_API_KEY", message)
            self.assertIn("DOPPEL_MODEL", message)
        finally:
            os.environ.pop(LIVE_CONFIRM_ENV, None)

    async def test_dry_run_never_requires_credentials(self) -> None:
        # The provider guard is evaluated before any product or network setup.
        os.environ.pop(LIVE_CONFIRM_ENV, None)
        _validate_provider_activation(
            live_provider=False,
            model="",
            base_url="",
            api_key="",
        )


@unittest.skipUnless(
    NEO4J_URI and NEO4J_USER and NEO4J_PASSWORD,
    "live Neo4j env (GRAPHITI_EVAL_NEO4J_*) not configured",
)
class DryRunNeo4jTest(unittest.IsolatedAsyncioTestCase):
    async def test_dry_run_reports_full_shapes(self) -> None:
        report = await run_smoke(
            neo4j_uri=NEO4J_URI,
            neo4j_user=NEO4J_USER,
            neo4j_password=NEO4J_PASSWORD,
            live_provider=False,
        )
        scenario = report["scenario"]
        self.assertEqual(scenario["mode"], "dry-run-fake")
        self.assertFalse(scenario["live_provider"])
        self.assertFalse(scenario["key_persisted"])
        self.assertEqual(scenario["hard_failure"], "")
        self.assertFalse(scenario["graph_edge_path_not_exercised"])
        self.assertGreaterEqual(scenario["nodes_created"], 2)
        self.assertGreaterEqual(scenario["edges_created"], 1)
        self.assertTrue(str(scenario["edge_valid_at"]).startswith("2025-01-01"))
        self.assertTrue(str(scenario["edge_invalid_at"]).startswith("2025-03-31"))
        self.assertEqual(scenario["logical_calls"], len(scenario["prompt_names"]))
        self.assertGreaterEqual(scenario["logical_calls"], 3)
        self.assertTrue(scenario["inside_interval_hit"])
        self.assertFalse(scenario["outside_interval_hit"])
        self.assertTrue(scenario["provenance_ok"])
        self.assertTrue(scenario["store_revalidation_ok"])
        self.assertTrue(scenario["scope_isolation_ok"])
        self.assertTrue(scenario["cleanup_performed"])
        self.assertIn("usage", scenario)
        self.assertTrue(scenario["gate"]["ok"])
        self.assertNotIn("Bearer", report.__str__())


if __name__ == "__main__":
    unittest.main()
