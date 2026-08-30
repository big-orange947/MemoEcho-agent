from __future__ import annotations

import os
import unittest

from doppel_eval.graph_e2e import _bigram_overlap, run_graph_e2e
from doppel_eval.replay import _load_doppel


class GraphE2ERunnerTest(unittest.IsolatedAsyncioTestCase):
    async def test_contract_backend_is_temporal_isolated_and_zero_paid(self) -> None:
        if not os.environ.get("DOPPEL_IMPORT_PATH"):
            self.skipTest("DOPPEL_IMPORT_PATH not set")
        dm = _load_doppel()
        if dm is None:
            self.skipTest("doppel_memory not importable")

        report = await run_graph_e2e(dm, backend="contract")

        self.assertEqual(report["runner"], "doppel.graph-e2e.v1")
        self.assertEqual(report["mode"], "contract-no-network")
        self.assertEqual(report["summary"]["scenarios"], 7)
        self.assertEqual(report["summary"]["passed_scenarios"], 7)
        self.assertEqual(report["summary"]["scope_leakage_failures"], 0)
        self.assertEqual(report["usage"]["llm_calls"], 0)
        self.assertEqual(report["usage"]["provider_tokens"], 0)
        self.assertEqual(
            report["usage"]["temporal_filter_calls"],
            report["usage"]["graph_search_calls"],
        )
        self.assertTrue(report["gate"]["ok"])
        self.assertTrue(all(item["provenance_ok"] for item in report["scenarios"]))
        self.assertTrue(
            all(item["semantic_binding_ok"] for item in report["scenarios"])
        )

    async def test_live_backend_requires_dedicated_credentials(self) -> None:
        if not os.environ.get("DOPPEL_IMPORT_PATH"):
            self.skipTest("DOPPEL_IMPORT_PATH not set")
        dm = _load_doppel()
        if dm is None:
            self.skipTest("doppel_memory not importable")

        with self.assertRaisesRegex(ValueError, "GRAPHITI_EVAL_NEO4J"):
            await run_graph_e2e(dm, backend="neo4j")


class ContractMatcherTest(unittest.TestCase):
    def test_matcher_is_generic_character_bigram_overlap(self) -> None:
        self.assertGreater(_bigram_overlap("长期住址", "长期住址是上海"), 0)
        self.assertGreater(_bigram_overlap("项目工作", "在深圳项目工作"), 0)
        self.assertEqual(_bigram_overlap("长期住址", "产品经理"), 0)


if __name__ == "__main__":
    unittest.main()
