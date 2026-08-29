"""Budgeted, cached structured-output helpers for synthetic E2E evaluation."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class ProviderBudgetExceeded(RuntimeError):
    """A provider request was stopped before transmission by the local budget."""


@dataclass(frozen=True)
class ProviderBudget:
    max_calls: int = 10
    max_input_tokens: int = 80_000
    max_output_tokens: int = 10_240
    max_total_tokens: int = 90_240
    max_output_tokens_per_call: int = 1_024

    def __post_init__(self) -> None:
        for name, value in self.__dict__.items():
            if int(value) <= 0:
                raise ValueError(f"{name} must be positive")


@dataclass
class ProviderUsageLedger:
    budget: ProviderBudget
    calls_attempted: int = 0
    responses_with_usage: int = 0
    provider_errors: int = 0
    cache_hits: int = 0
    cache_writes: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cached_input_tokens: int = 0
    cache_miss_input_tokens: int = 0
    reasoning_tokens: int = 0
    stopped_reason: str = ""
    estimates: list[int] = field(default_factory=list)

    def reserve_call(self, request: Any) -> None:
        estimated_input = _estimate_tokens(request)
        projected_output = self.budget.max_output_tokens_per_call
        checks = (
            (self.calls_attempted + 1, self.budget.max_calls, "max_calls"),
            (
                self.input_tokens + estimated_input,
                self.budget.max_input_tokens,
                "max_input_tokens",
            ),
            (
                self.output_tokens + projected_output,
                self.budget.max_output_tokens,
                "max_output_tokens",
            ),
            (
                self.total_tokens + estimated_input + projected_output,
                self.budget.max_total_tokens,
                "max_total_tokens",
            ),
        )
        for projected, limit, name in checks:
            if projected > limit:
                self.stopped_reason = name
                raise ProviderBudgetExceeded(
                    f"provider budget would exceed {name}: {projected}>{limit}"
                )
        self.calls_attempted += 1
        self.estimates.append(estimated_input)

    def observe_usage(self, usage: Mapping[str, int]) -> None:
        self.responses_with_usage += 1
        for name in (
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "cached_input_tokens",
            "cache_miss_input_tokens",
            "reasoning_tokens",
        ):
            value = usage.get(name, 0)
            if isinstance(value, int) and value >= 0:
                setattr(self, name, getattr(self, name) + value)
        limits = (
            (self.input_tokens, self.budget.max_input_tokens, "max_input_tokens"),
            (self.output_tokens, self.budget.max_output_tokens, "max_output_tokens"),
            (self.total_tokens, self.budget.max_total_tokens, "max_total_tokens"),
        )
        for actual, limit, name in limits:
            if actual > limit and not self.stopped_reason:
                self.stopped_reason = name

    def report(self) -> dict[str, Any]:
        return {
            "budget": self.budget.__dict__,
            "calls_attempted": self.calls_attempted,
            "responses_with_usage": self.responses_with_usage,
            "usage_missing_calls": max(
                self.calls_attempted - self.responses_with_usage, 0
            ),
            "provider_errors": self.provider_errors,
            "cache_hits": self.cache_hits,
            "cache_writes": self.cache_writes,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "cache_miss_input_tokens": self.cache_miss_input_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "estimated_input_tokens": sum(self.estimates),
            "stopped_reason": self.stopped_reason,
            "within_budget": not self.stopped_reason,
        }


class BudgetedCachedStructuredOutputModel:
    """Wrap a provider with preflight budgets and synthetic-response caching."""

    name = "doppel.eval-budgeted-cached-model"

    def __init__(
        self,
        model: Any,
        ledger: ProviderUsageLedger,
        *,
        cache_dir: Path | None = None,
    ) -> None:
        self.model = model
        self.ledger = ledger
        self.cache_dir = cache_dir
        model_name = str(getattr(model, "name", type(model).__name__))
        model_version = str(getattr(model, "version", "unknown"))
        self.version = hashlib.sha256(
            f"1:{model_name}:{model_version}".encode()
        ).hexdigest()[:20]

    async def generate(self, request: Any) -> Mapping[str, Any]:
        cache_path = self._cache_path(request)
        if cache_path is not None and cache_path.is_file():
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if isinstance(cached, Mapping):
                self.ledger.cache_hits += 1
                return dict(cached)
        self.ledger.reserve_call(request)
        try:
            result = await self.model.generate(request)
        except Exception:
            self.ledger.provider_errors += 1
            raise
        normalized = dict(result)
        if cache_path is not None:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            temp = cache_path.with_suffix(".tmp")
            temp.write_text(
                json.dumps(normalized, ensure_ascii=False, sort_keys=True),
                encoding="utf-8",
            )
            temp.replace(cache_path)
            self.ledger.cache_writes += 1
        return normalized

    def _cache_path(self, request: Any) -> Path | None:
        if self.cache_dir is None:
            return None
        if hasattr(request, "model_dump"):
            request = request.model_dump(mode="json")
        payload = {
            "model_name": str(getattr(self.model, "name", "")),
            "model_version": str(getattr(self.model, "version", "")),
            "request": request,
        }
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return self.cache_dir / f"{hashlib.sha256(encoded).hexdigest()}.json"


class RetryEmptyJsonOnceModel:
    """Retry only the provider's documented intermittent invalid JSON failure."""

    name = "doppel.eval-retry-empty-json-once"

    def __init__(self, model: Any) -> None:
        self.model = model
        self.version = f"1.{getattr(model, 'version', 'unknown')}"

    async def generate(self, request: Any) -> Mapping[str, Any]:
        try:
            return await self.model.generate(request)
        except Exception as exc:
            if getattr(exc, "code", "") != "invalid_content_json":
                raise
            return await self.model.generate(request)


def _estimate_tokens(request: Any) -> int:
    if hasattr(request, "model_dump"):
        request = request.model_dump(mode="json")
    encoded = json.dumps(
        request, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    # Conservative provider-neutral preflight. Actual usage always replaces this
    # in reports when the endpoint supplies usage fields.
    return max(1, math.ceil(len(encoded) / 3))
