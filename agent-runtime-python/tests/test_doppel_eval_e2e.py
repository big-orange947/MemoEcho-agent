from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from doppel_eval.e2e import run_e2e
from doppel_eval.provider import ProviderBudget
from doppel_eval.replay import _load_doppel


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


class E2ERunnerTest(unittest.IsolatedAsyncioTestCase):
    async def test_noise_scene_runs_without_gold_memory_or_network(self) -> None:
        if not os.environ.get("DOPPEL_IMPORT_PATH"):
            self.skipTest("DOPPEL_IMPORT_PATH not set")
        dm = _load_doppel()
        if dm is None:
            self.skipTest("doppel_memory not importable")
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(
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
        self.assertTrue(report["gate"]["strict_passed"])


if __name__ == "__main__":
    unittest.main()
