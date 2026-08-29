from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from doppel_eval.provider import (
    BudgetedCachedStructuredOutputModel,
    ProviderBudget,
    ProviderBudgetExceeded,
    ProviderUsageLedger,
    RetryEmptyJsonOnceModel,
)


class _Request:
    def __init__(self, value: str = "hello") -> None:
        self.value = value

    def model_dump(self, *, mode: str) -> dict:
        assert mode == "json"
        return {"input": self.value}


class _Model:
    name = "fake-provider"
    version = "1"

    def __init__(self, *, fail_once: bool = False) -> None:
        self.calls = 0
        self.fail_once = fail_once

    async def generate(self, request) -> dict:
        self.calls += 1
        if self.fail_once and self.calls == 1:
            error = RuntimeError("empty")
            error.code = "invalid_content_json"  # type: ignore[attr-defined]
            raise error
        return {"memories": [], "value": request.value}


class ProviderBudgetTest(unittest.IsolatedAsyncioTestCase):
    async def test_cache_avoids_a_second_provider_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            model = _Model()
            ledger = ProviderUsageLedger(ProviderBudget(max_calls=1))
            wrapped = BudgetedCachedStructuredOutputModel(
                model, ledger, cache_dir=Path(tmp)
            )

            first = await wrapped.generate(_Request())
            second = await wrapped.generate(_Request())

            self.assertEqual(first, second)
            self.assertEqual(model.calls, 1)
            self.assertEqual(ledger.calls_attempted, 1)
            self.assertEqual(ledger.cache_hits, 1)
            self.assertEqual(ledger.cache_writes, 1)

    async def test_preflight_stops_before_exceeding_call_budget(self) -> None:
        model = _Model()
        ledger = ProviderUsageLedger(ProviderBudget(max_calls=1))
        wrapped = BudgetedCachedStructuredOutputModel(model, ledger)

        await wrapped.generate(_Request("first"))
        with self.assertRaises(ProviderBudgetExceeded):
            await wrapped.generate(_Request("second"))

        self.assertEqual(model.calls, 1)
        self.assertEqual(ledger.stopped_reason, "max_calls")

    async def test_invalid_json_is_retried_only_once_and_charged_twice(self) -> None:
        model = _Model(fail_once=True)
        ledger = ProviderUsageLedger(ProviderBudget(max_calls=2))
        budgeted = BudgetedCachedStructuredOutputModel(model, ledger)
        wrapped = RetryEmptyJsonOnceModel(budgeted)

        result = await wrapped.generate(_Request())

        self.assertEqual(result["memories"], [])
        self.assertEqual(model.calls, 2)
        self.assertEqual(ledger.calls_attempted, 2)
        self.assertEqual(ledger.provider_errors, 1)

    def test_usage_report_preserves_provider_and_cache_token_counts(self) -> None:
        ledger = ProviderUsageLedger(ProviderBudget())
        ledger.reserve_call(_Request())
        ledger.observe_usage(
            {
                "input_tokens": 100,
                "output_tokens": 20,
                "total_tokens": 120,
                "cached_input_tokens": 80,
                "cache_miss_input_tokens": 20,
                "reasoning_tokens": 0,
            }
        )

        report = ledger.report()
        self.assertEqual(report["total_tokens"], 120)
        self.assertEqual(report["cached_input_tokens"], 80)
        self.assertEqual(report["usage_missing_calls"], 0)
        self.assertTrue(report["within_budget"])


if __name__ == "__main__":
    unittest.main()
