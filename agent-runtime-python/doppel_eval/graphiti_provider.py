"""Evaluation-only budgeted/cached Graphiti LLM client (no real requests by default).

Why not Graphiti's own clients:

- ``OpenAIClient`` targets the OpenAI *Responses* API (``responses.parse`` with
  ``text_format``); DeepSeek only speaks ``/chat/completions``.
- ``OpenAIGenericClient`` does speak chat/completions and supports
  ``structured_output_mode="json_object"``, but it raises NotImplementedError
  for caching, has no provider-usage ledger, lets Graphiti's tenacity retry up
  to 4 times outside any budget, and requests up to 16384 output tokens.

This client implements the Graphiti ``LLMClient`` protocol with:

- OpenAI-compatible ``POST /chat/completions`` (httpx, injectable for tests)
- ``response_format={"type": "json_object"}`` — never ``json_schema``
- the Pydantic JSON schema injected into the user prompt
- ``max_tokens`` capped at ``budget.max_output_tokens_per_call``
- ``temperature=0`` and DeepSeek ``thinking={"type": "disabled"}``
- an atomic per-attempt budget reservation (asyncio.Lock), including retries
- content-addressed disk cache whose key/content never contain the API key
- at most one internal retry, each retry re-reserving budget
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from graphiti_core.llm_client.client import LLMClient, get_extraction_language_instruction
from graphiti_core.llm_client.config import DEFAULT_MAX_TOKENS, ModelSize
from graphiti_core.prompts.models import Message
from pydantic import BaseModel

logger = logging.getLogger(__name__)

CLIENT_VERSION = "doppel.graphiti-budgeted-cache.v1"
DEFAULT_CACHE_DIR = Path("data/doppel/graphiti-e2e-cache")

#: HTTP status codes that may succeed on a single retry.
RETRYABLE_STATUSES = {408, 409, 425, 429}
RETRYABLE_TRANSPORT = ("timeout", "connect", "network", "resolve")

#: We refuse to send Graphiti's 16K default; every call is capped.
DEFAULT_OUTPUT_CAP = 1_024


class GraphitiProviderHardFailure(RuntimeError):
    """A provider-level failure that must gate the run (never a quality miss)."""

    def __init__(self, message: str, *, code: str = "terminal") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class GraphitiProviderBudget:
    max_calls: int = 10
    max_input_tokens: int = 80_000
    max_output_tokens: int = 10_240
    max_total_tokens: int = 90_240
    max_output_tokens_per_call: int = DEFAULT_OUTPUT_CAP

    def __post_init__(self) -> None:
        for name, value in self.__dict__.items():
            if int(value) <= 0:
                raise ValueError(f"{name} must be positive")


@dataclass(frozen=True)
class GraphitiCallReservation:
    """An in-flight budget hold for one real HTTP attempt."""

    reservation_id: str
    estimated_input_tokens: int
    reserved_output_tokens: int
    reserved_total_tokens: int


class GraphitiUsageLedger:
    """Thread-safe (asyncio) usage accounting; one ledger per client."""

    def __init__(self, budget: GraphitiProviderBudget) -> None:
        self.budget = budget
        self.calls_attempted = 0
        self.calls_succeeded = 0
        self.cache_hits = 0
        self.cache_writes = 0
        self.provider_errors = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.total_tokens = 0
        self.conservative_charged_tokens = 0
        self.validation_errors = 0
        self.stopped_reason = ""
        self._lock = asyncio.Lock()
        self._estimates: list[int] = []
        self._reserved_input_tokens = 0
        self._reserved_output_tokens = 0
        self._reserved_total_tokens = 0
        self._active_reservations: dict[str, GraphitiCallReservation] = {}

    async def reserve_call(
        self, estimated_input_tokens: int
    ) -> GraphitiCallReservation:
        """Atomically reserve one real HTTP attempt; raise when over budget.

        Both *actual* usage and *in-flight reserved* amounts count against the
        limits, so concurrent calls can never jointly overshoot the budget.
        """
        reservation = GraphitiCallReservation(
            reservation_id=uuid4().hex[:16],
            estimated_input_tokens=int(estimated_input_tokens),
            reserved_output_tokens=self.budget.max_output_tokens_per_call,
            reserved_total_tokens=(
                int(estimated_input_tokens) + self.budget.max_output_tokens_per_call
            ),
        )
        async with self._lock:
            checks = (
                (self.calls_attempted + 1, self.budget.max_calls, "max_calls"),
                (
                    self._reserved_input_tokens
                    + self.input_tokens
                    + reservation.estimated_input_tokens,
                    self.budget.max_input_tokens,
                    "max_input_tokens",
                ),
                (
                    self._reserved_output_tokens
                    + self.output_tokens
                    + reservation.reserved_output_tokens,
                    self.budget.max_output_tokens,
                    "max_output_tokens",
                ),
                (
                    self._reserved_total_tokens
                    + self.total_tokens
                    + reservation.reserved_total_tokens,
                    self.budget.max_total_tokens,
                    "max_total_tokens",
                ),
            )
            for projected, limit, name in checks:
                if projected > limit:
                    self.stopped_reason = name
                    raise GraphitiProviderHardFailure(
                        f"provider budget exceeded ({name}): {projected}>{limit}"
                    )
            self.calls_attempted += 1
            self._estimates.append(reservation.estimated_input_tokens)
            self._reserved_input_tokens += reservation.estimated_input_tokens
            self._reserved_output_tokens += reservation.reserved_output_tokens
            self._reserved_total_tokens += reservation.reserved_total_tokens
            self._active_reservations[reservation.reservation_id] = reservation
        return reservation

    async def settle_success(
        self, reservation: GraphitiCallReservation, usage: dict[str, int] | None
    ) -> None:
        """Full success (HTTP + parse): release the reservation and add usage."""
        async with self._lock:
            self._release_locked(reservation)
            self._add_usage_locked(usage)
            self.calls_succeeded += 1

    async def settle_failure(
        self,
        reservation: GraphitiCallReservation,
        usage: dict[str, int] | None = None,
        *,
        validation: bool = False,
    ) -> None:
        """Failed call (HTTP error, invalid JSON, or validation failure).

        The call already happened: provider usage (when present) is charged,
        otherwise the reserved estimate is conservatively charged.
        """
        async with self._lock:
            self._release_locked(reservation)
            if usage:
                self._add_usage_locked(usage)
            else:
                self.conservative_charged_tokens += (
                    reservation.estimated_input_tokens
                    + reservation.reserved_output_tokens
                )
            self.provider_errors += 1
            if validation:
                self.validation_errors += 1

    async def release_cancelled(
        self, reservation: GraphitiCallReservation, *, http_started: bool
    ) -> None:
        """Task cancellation: free the reservation only if HTTP never started."""
        async with self._lock:
            if not http_started:
                self._release_locked(reservation)
                return
            self._release_locked(reservation)
            self.conservative_charged_tokens += (
                reservation.estimated_input_tokens
                + reservation.reserved_output_tokens
            )
            self.provider_errors += 1

    def _release_locked(self, reservation: GraphitiCallReservation) -> None:
        self._reserved_input_tokens -= reservation.estimated_input_tokens
        self._reserved_output_tokens -= reservation.reserved_output_tokens
        self._reserved_total_tokens -= reservation.reserved_total_tokens
        self._active_reservations.pop(reservation.reservation_id, None)

    def _add_usage_locked(self, usage: dict[str, int] | None) -> None:
        if not usage:
            return
        self.input_tokens += int(usage.get("prompt_tokens", 0) or 0)
        self.output_tokens += int(usage.get("completion_tokens", 0) or 0)
        self.total_tokens += int(usage.get("total_tokens", 0) or 0)
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
            "calls_attempted": self.calls_attempted,
            "calls_succeeded": self.calls_succeeded,
            "cache_hits": self.cache_hits,
            "cache_writes": self.cache_writes,
            "provider_errors": self.provider_errors,
            "validation_errors": self.validation_errors,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "reserved_input_tokens": self._reserved_input_tokens,
            "reserved_output_tokens": self._reserved_output_tokens,
            "reserved_total_tokens": self._reserved_total_tokens,
            "active_reservations": len(self._active_reservations),
            "conservative_charged_tokens": self.conservative_charged_tokens,
            "estimated_input_tokens": sum(self._estimates),
            "stopped_reason": self.stopped_reason,
            "within_budget": not self.stopped_reason,
        }


def _estimate_tokens(messages: list[dict[str, str]], schema_text: str) -> int:
    encoded = json.dumps(
        {"messages": messages, "schema": schema_text},
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")
    return max(1, math.ceil(len(encoded) / 3))


def _strip_code_fences(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[a-zA-Z]*\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    return stripped.strip()


class BudgetedCachedGraphitiLLMClient(LLMClient):
    """Graphiti ``LLMClient`` backed by a budgeted, cached chat-completions call."""

    def __init__(
        self,
        *,
        model: str,
        base_url: str,
        api_key: str = "",
        http_client: httpx.AsyncClient | None = None,
        budget: GraphitiProviderBudget | None = None,
        cache_dir: Path | None = DEFAULT_CACHE_DIR,
        temperature: float = 0.0,
        thinking: str = "disabled",
        max_tokens_parameter: str = "max_tokens",
        max_retries: int = 1,
        client_version: str = CLIENT_VERSION,
    ) -> None:
        if not str(model or "").strip():
            raise ValueError("model is required")
        if not str(base_url or "").strip():
            raise ValueError("base_url is required")
        super().__init__(config=None, cache=False)
        self.model = str(model).strip()
        self.small_model = self.model
        self.max_tokens = DEFAULT_MAX_TOKENS  # Graphiti's default; capped per call
        self._base_url = str(base_url).strip().rstrip("/")
        self._api_key = str(api_key or "").strip()
        self._http = http_client or httpx.AsyncClient(timeout=60.0)
        self._budget = budget or GraphitiProviderBudget()
        self._ledger = GraphitiUsageLedger(self._budget)
        self._cache_dir = Path(cache_dir) if cache_dir else None
        self._temperature = float(temperature)
        self._thinking = str(thinking or "").strip()
        self._max_tokens_parameter = str(max_tokens_parameter or "max_tokens")
        self._max_retries = max(int(max_retries), 0)
        self._client_version = str(client_version or CLIENT_VERSION)
        self._last_usage: dict[str, int] | None = None
        self._logical_calls = 0
        self._prompt_names: list[str] = []

    @property
    def logical_calls(self) -> int:
        """Graphiti requests observed, including requests served from cache."""
        return self._logical_calls

    @property
    def prompt_names(self) -> list[str]:
        """Graphiti prompt identities in call order (safe copy for reports)."""
        return list(self._prompt_names)

    # ---------------------------------------------------------------- protocol

    async def _generate_response(
        self,
        messages: list[Message],
        response_model: type[BaseModel] | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        model_size: ModelSize = ModelSize.medium,
    ) -> dict[str, Any]:
        """ABC requirement. Never used: ``generate_response`` is overridden."""
        raise NotImplementedError(
            "BudgetedCachedGraphitiLLMClient requires generate_response()"
        )

    async def generate_response(
        self,
        messages: list[Message],
        response_model: type[BaseModel] | None = None,
        max_tokens: int | None = None,
        model_size: ModelSize = ModelSize.medium,
        group_id: str | None = None,
        prompt_name: str | None = None,
        *,
        attribute_extraction: bool = False,
    ) -> dict[str, Any]:
        """Single budgeted call with one internal retry and disk caching."""
        self._logical_calls += 1
        self._prompt_names.append(str(prompt_name or "<unspecified>"))

        # Work on safe copies: the base pipeline mutates message content.
        working = [message.model_copy(deep=True) for message in messages]
        self._apply_attribute_extraction_preamble(working, attribute_extraction)

        schema_text = ""
        if response_model is not None:
            schema_text = json.dumps(
                response_model.model_json_schema(), ensure_ascii=False, sort_keys=True
            )
            working[-1].content += (
                "\n\nRespond with a JSON object in the following format:\n\n"
                f"{schema_text}"
            )
        working[0].content += get_extraction_language_instruction(group_id)
        for message in working:
            message.content = self._clean_input(message.content)

        requested = int(max_tokens or self.max_tokens or DEFAULT_MAX_TOKENS)
        effective = min(requested, self._budget.max_output_tokens_per_call)
        messages_payload = [
            {"role": message.role, "content": message.content} for message in working
        ]

        cache_key = self._cache_key(
            messages_payload, schema_text, effective, model_size
        )
        cached = self._read_cache(cache_key)
        if cached is not None:
            self._ledger.cache_hits += 1
            return self._validate(cached, response_model, prompt_name)

        estimate = _estimate_tokens(messages_payload, schema_text)
        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            reservation = await self._ledger.reserve_call(estimate)
            http_started = False
            self._last_usage: dict[str, int] | None = None
            try:
                http_started = True  # before the attempt; cancellation is conservative
                result = await self._post_chat(
                    messages_payload, schema_text, effective, model_size
                )
                validated = self._validate(result, response_model, prompt_name)
                await self._ledger.settle_success(reservation, self._last_usage)
                self._write_cache(cache_key, result)
                return validated
            except asyncio.CancelledError:
                await self._ledger.release_cancelled(
                    reservation, http_started=http_started
                )
                raise
            except Exception as exc:  # noqa: BLE001 - classified below
                is_validation = (
                    isinstance(exc, GraphitiProviderHardFailure)
                    and str(exc).startswith("response_validation_error")
                )
                await self._ledger.settle_failure(
                    reservation, self._last_usage, validation=is_validation
                )
                if (
                    attempt < self._max_retries
                    and isinstance(exc, GraphitiProviderHardFailure)
                    and _is_retryable(exc)
                ):
                    last_error = exc
                    continue
                raise
        raise GraphitiProviderHardFailure(
            f"all retries exhausted: {last_error}"
        )

    # ---------------------------------------------------------------- internals

    async def _post_chat(
        self,
        messages: list[dict[str, str]],
        schema_text: str,
        max_tokens: int,
        model_size: ModelSize,
    ) -> dict[str, Any]:
        """One real HTTP attempt against an OpenAI-compatible endpoint."""
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self._temperature,
            "response_format": {"type": "json_object"},
            self._max_tokens_parameter: max_tokens,
        }
        if self._thinking:
            payload["thinking"] = {"type": self._thinking}
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        try:
            response = await self._http.post(
                f"{self._base_url}/chat/completions",
                json=payload,
                headers=headers,
            )
        except httpx.TimeoutException as exc:
            raise GraphitiProviderHardFailure("transport_timeout", code="retryable") from exc
        except httpx.TransportError as exc:
            raise GraphitiProviderHardFailure("transport_error", code="retryable") from exc

        if response.status_code >= 400:
            raise GraphitiProviderHardFailure(
                f"http_{response.status_code}",
                code="retryable" if (
                    response.status_code in RETRYABLE_STATUSES or response.status_code >= 500
                ) else "terminal",
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise GraphitiProviderHardFailure("invalid_json_response") from exc
        if isinstance(data, dict) and isinstance(data.get("usage"), dict):
            self._last_usage = {
                str(key): int(value)
                for key, value in data["usage"].items()
                if isinstance(value, int) and value >= 0
            }
        choices = (data or {}).get("choices") or []
        if not choices:
            raise GraphitiProviderHardFailure("empty_choices")
        content = str(choices[0].get("message", {}).get("content") or "").strip()
        if not content:
            raise GraphitiProviderHardFailure("empty_content")
        try:
            return json.loads(_strip_code_fences(content))
        except ValueError as exc:
            raise GraphitiProviderHardFailure("invalid_content_json") from exc

    def _validate(
        self,
        raw: dict[str, Any],
        response_model: type[BaseModel] | None,
        prompt_name: str | None,
    ) -> dict[str, Any]:
        if response_model is None:
            return raw
        try:
            model = response_model.model_validate(raw)
        except Exception as exc:  # noqa: BLE001 - validation boundary
            raise GraphitiProviderHardFailure(
                f"response_validation_error ({type(exc).__name__})"
            ) from exc
        return model.model_dump()

    # ---------------------------------------------------------------- cache

    def _cache_key(
        self,
        messages: list[dict[str, str]],
        schema_text: str,
        max_tokens: int,
        model_size: ModelSize | None,
    ) -> str:
        payload = {
            "client_version": self._client_version,
            "model": self.model,
            "base_url": self._base_url,
            "messages": messages,
            "schema": schema_text,
            "max_tokens": max_tokens,
            "temperature": self._temperature,
            "mode": "json_object",
            "thinking": self._thinking,
            "model_size": str(getattr(model_size, "value", model_size)),
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _cache_path(self, key: str) -> Path:
        return self._cache_dir / f"{key}.json"

    def _read_cache(self, key: str) -> dict[str, Any] | None:
        if self._cache_dir is None:
            return None
        path = self._cache_path(key)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            logger.warning("cache read failed for %s", path.name)
            return None
        return data if isinstance(data, dict) else None

    def _write_cache(self, key: str, data: dict[str, Any]) -> None:
        if self._cache_dir is None:
            return
        try:
            path = self._cache_path(key)
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_suffix(".tmp")
            temp.write_text(
                json.dumps(data, ensure_ascii=False, sort_keys=True),
                encoding="utf-8",
            )
            temp.replace(path)
            self._ledger.cache_writes += 1
        except OSError:
            logger.warning("cache write failed for %s", key[:12])

    # ---------------------------------------------------------------- accessors

    @property
    def ledger(self) -> GraphitiUsageLedger:
        return self._ledger

    async def aclose(self) -> None:
        try:
            await self._http.aclose()
        except Exception:  # noqa: BLE001
            pass


def _is_retryable(exc: GraphitiProviderHardFailure) -> bool:
    return str(getattr(exc, "code", "") or "") == "retryable"
