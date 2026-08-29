"""Doppel shadow worker: observe events without influencing replies.

Mirrors the existing ``MemoryCandidateExtractor.schedule`` pattern
(fire-and-forget asyncio task).  Ingest is decoupled from processing:

- ``schedule()`` only appends to the persistent inbox (fast, never blocks
  the reply path) and wakes one consumer.
- A bounded set of consumer tasks claims pending rows, runs the Doppel
  pipeline, and marks succeeded / failed_retryable / dead_letter.
- On restart, ``requeue_stale()`` moves rows that were still
  pending/processing back to pending so the pipeline eventually runs.
- The Doppel engine is an instance dependency (not a module global), so
  ``shutdown()`` can close the client and the store.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Any

from app.integrations.doppel.bridge import bridge_payload
from app.integrations.doppel.shadow_store import STATUS_FAILED_RETRYABLE, ShadowStore
from app.schemas.events import UnifiedEvent

logger = logging.getLogger(__name__)


class DoppelShadowWorker:
    """Observes unified events for Doppel evaluation (never replies)."""

    def __init__(
        self,
        store: ShadowStore,
        *,
        enabled: bool = True,
        doppel_import_path: str | None = None,
        max_pending: int = 10_000,
        consumers: int = 2,
        retry_delay_seconds: float = 0.25,
        extract_enabled: bool = False,
        model: str = "",
        base_url: str = "https://api.openai.com/v1",
        api_key: str = "",
        schema_mode: str = "json_schema",
        max_completion_tokens: int | None = None,
        max_tokens_parameter: str = "max_completion_tokens",
        thinking: str | None = None,
    ) -> None:
        self._store = store
        self._enabled = enabled
        self._doppel_import_path = doppel_import_path
        self._max_pending = max(int(max_pending), 1)
        self._consumers = max(int(consumers), 1)
        self._retry_delay_seconds = max(float(retry_delay_seconds), 0.0)
        self._extract_enabled = bool(extract_enabled)
        self._model = str(model or "").strip()
        self._base_url = str(base_url or "").strip()
        self._api_key = str(api_key or "").strip()
        self._schema_mode = str(schema_mode or "json_schema").strip()
        self._max_completion_tokens = max_completion_tokens
        self._max_tokens_parameter = str(
            max_tokens_parameter or "max_completion_tokens"
        ).strip()
        self._thinking = str(thinking or "").strip() or None
        self._queue: asyncio.Queue[str] | None = None
        self._consumer_tasks: list[asyncio.Task] = []
        self._background_tasks: set[asyncio.Task] = set()
        self._engine: _DoppelEngine | None = None
        self._engine_lock: asyncio.Lock | None = None
        self._closing = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    def _ensure_loop_objects(self) -> None:
        if self._queue is None:
            self._queue = asyncio.Queue(maxsize=self._max_pending)
            self._consumer_tasks = [
                asyncio.create_task(self._consume(), name=f"doppel-shadow-{index}")
                for index in range(self._consumers)
            ]

    async def open(self) -> None:
        """Open the store, recover stale rows, and start consumers."""
        self._closing = False
        await self._store.open()
        await self._store.requeue_stale()
        if self._enabled:
            self._ensure_loop_objects()

    async def shutdown(self) -> None:
        """Stop accepting work, persist/drain queued events, then close resources."""
        self._closing = True
        if self._background_tasks:
            await asyncio.gather(*tuple(self._background_tasks), return_exceptions=True)
        if self._queue is not None and self._consumer_tasks:
            try:
                await asyncio.wait_for(self._queue.join(), timeout=10.0)
            except TimeoutError:
                logger.warning("timed out while draining Doppel shadow queue")
        for task in self._consumer_tasks:
            task.cancel()
        if self._consumer_tasks:
            await asyncio.gather(*self._consumer_tasks, return_exceptions=True)
        self._consumer_tasks = []
        self._queue = None
        self._background_tasks.clear()
        if self._engine is not None:
            try:
                await self._engine.close()
            except Exception:  # noqa: BLE001
                logger.warning("doppel engine close failed", exc_info=True)
            self._engine = None
        await self._store.close()

    def schedule(self, event: UnifiedEvent) -> bool:
        """Persist the event and wake a consumer; returns False when disabled."""
        if not self._enabled:
            return False
        if self._closing:
            logger.warning("Doppel shadow is shutting down; event not scheduled")
            return False
        try:
            self._ensure_loop_objects()
            payload = bridge_payload(event)
            event_id = str(payload["event_id"] or "")
            task = asyncio.create_task(self._persist_and_process(payload, event_id))
            self._background_tasks.add(task)
            task.add_done_callback(self._finish_task)
            return True
        except Exception:  # noqa: BLE001 - shadow must never break the reply path
            logger.warning("Doppel shadow schedule failed; event dropped", exc_info=True)
            return False

    def _finish_task(self, task: asyncio.Task) -> None:
        self._background_tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.warning("shadow persist task failed: %s", exc)

    async def _persist_and_process(self, payload: dict, event_id: str) -> None:
        """Append to the persistent inbox (idempotent) then process via queue."""
        if payload["errors"]:
            await self._store.trace(
                event_id, "dead_letter", {"errors": payload["errors"]}
            )
            return
        inserted = await self._store.append(event_id, payload)
        if inserted is None:
            raise RuntimeError(f"shadow inbox append failed for {event_id}")
        if not inserted:
            await self._store.trace(event_id, "duplicate", {})
            return
        assert self._queue is not None
        try:
            self._queue.put_nowait(event_id)
        except asyncio.QueueFull:
            # A queued wake token already causes a consumer to drain every
            # pending row, so the persisted row cannot be stranded here.
            logger.debug("shadow wake queue full; existing token will drain inbox")

    async def _consume(self) -> None:
        # Restart recovery: drain any persisted pending rows first.
        await self._drain_pending()
        while True:
            try:
                await self._queue.get()
            except asyncio.CancelledError:
                return
            try:
                await self._drain_pending()
            finally:
                self._queue.task_done()

    async def _drain_pending(self) -> None:
        while True:
            item = await self._store.claim_next()
            if item is None:
                return
            await self._run_pipeline(item)

    async def _run_pipeline(self, item: dict) -> None:
        event_id = str(item["event_id"] or "")
        payload = item["payload"] or {}
        error = ""
        succeeded = False
        try:
            await self._run_doppel_pipeline(payload)
            succeeded = True
        except Exception as exc:  # noqa: BLE001 - shadow isolation boundary
            error = f"{type(exc).__name__}: {exc}"
            logger.warning("doppel shadow pipeline failed for %s: %s", event_id, error)
        status = await self._store.complete(event_id, succeeded=succeeded, error=error)
        if status == STATUS_FAILED_RETRYABLE and not self._closing:
            retry_task = asyncio.create_task(
                self._retry_later(event_id, int(item.get("attempts") or 1)),
                name=f"doppel-shadow-retry-{event_id}",
            )
            self._background_tasks.add(retry_task)
            retry_task.add_done_callback(self._finish_task)

    async def _retry_later(self, event_id: str, attempts: int) -> None:
        delay = self._retry_delay_seconds * (2 ** max(attempts - 1, 0))
        if delay:
            await asyncio.sleep(delay)
        if self._closing or not await self._store.requeue_retryable(event_id):
            return
        if self._queue is None:
            return
        try:
            self._queue.put_nowait(event_id)
        except asyncio.QueueFull:
            pass

    async def _run_doppel_pipeline(self, payload: dict[str, Any]) -> None:
        engine = self._engine
        if engine is None:
            if self._engine_lock is None:
                self._engine_lock = asyncio.Lock()
            async with self._engine_lock:
                engine = self._engine
                if engine is None:
                    engine = _build_engine(
                        self._doppel_import_path,
                        extract_enabled=self._extract_enabled,
                        model=self._model,
                        base_url=self._base_url,
                        api_key=self._api_key,
                        schema_mode=self._schema_mode,
                        max_completion_tokens=self._max_completion_tokens,
                        max_tokens_parameter=self._max_tokens_parameter,
                        thinking=self._thinking,
                    )
                    self._engine = engine
        if engine is None:
            await self._store.trace(
                payload.get("event_id", ""),
                "doppel_skipped",
                {"reason": "engine_unavailable"},
            )
            raise RuntimeError("doppel_memory is unavailable")
        outcome = await engine.observe(payload)
        detail = (
            outcome if isinstance(outcome, dict) else {"event_status": str(outcome)}
        )
        detail["scope"] = payload.get("scope")
        await self._store.trace(
            payload.get("event_id", ""),
            "doppel_ingested",
            detail,
        )


class _DoppelEngine:
    """Instance-owned adapter around doppel_memory."""

    def __init__(
        self,
        module: Any,
        client: Any,
        *,
        extractor: Any | None = None,
        provider: Any | None = None,
    ) -> None:
        self._module = module
        self._client = client
        self._extractor = extractor
        self._provider = provider

    async def observe(self, payload: dict[str, Any]) -> dict[str, Any]:
        scope = _scope_object(self._module, payload.get("scope") or {})
        message = _message_object(self._module, payload.get("message") or {})
        if scope is None or message is None:
            raise ValueError("bridge payload cannot construct Doppel scope/message")
        result = await self._client.ingest(scope, message)
        status = getattr(result, "status", "")
        if hasattr(status, "value"):
            status = status.value
        normalized = str(status or "unknown")
        if normalized not in {"created", "updated", "duplicate"}:
            raise RuntimeError(f"Doppel ingest returned {normalized}")
        outcome: dict[str, Any] = {
            "event_status": normalized,
            "extraction_enabled": self._extractor is not None,
            "proposal_count": 0,
            "memory_write_count": 0,
        }
        if self._extractor is None or normalized == "duplicate":
            return outcome
        processing = await self._client.process(
            scope,
            message,
            processors=[self._extractor],
            allowed_scopes=[scope.user_scope()],
        )
        errors = list(getattr(processing, "errors", ()) or ())
        if errors:
            messages = [str(getattr(error, "message", error)) for error in errors]
            raise RuntimeError("Doppel extraction failed: " + "; ".join(messages))
        writes = list(getattr(processing, "write_results", ()) or ())
        failed_statuses: list[str] = []
        for write in writes:
            write_status = getattr(write, "status", "")
            if hasattr(write_status, "value"):
                write_status = write_status.value
            value = str(write_status or "unknown")
            if value not in {"created", "updated", "duplicate"}:
                failed_statuses.append(value)
        if failed_statuses:
            raise RuntimeError(
                "Doppel memory writes failed: " + ", ".join(failed_statuses)
            )
        outcome["proposal_count"] = len(getattr(processing, "proposals", ()) or ())
        outcome["memory_write_count"] = len(writes)
        return outcome

    async def close(self) -> None:
        if self._provider is not None:
            try:
                await self._provider.aclose()
            except Exception:  # noqa: BLE001
                pass
        try:
            await self._client.close()
        except Exception:  # noqa: BLE001
            pass


def _scope_object(module: Any, scope: dict) -> Any | None:
    try:
        return module.MemoryScope(**scope)
    except Exception:  # noqa: BLE001
        return None


def _message_object(module: Any, message: dict) -> Any | None:
    try:
        return module.ChatMessage(**message)
    except Exception:  # noqa: BLE001
        return None


def _load_doppel_module(import_path: str | None) -> Any | None:
    path = import_path or os.environ.get("DOPPEL_IMPORT_PATH", "")
    if path:
        path_obj = Path(path)
        if path_obj.is_dir() and str(path_obj) not in sys.path:
            sys.path.insert(0, str(path_obj))
    try:
        import doppel_memory  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        logger.info(
            "doppel_memory not importable; shadow runs in capture-only mode: %s", exc
        )
        return None
    return sys.modules["doppel_memory"]


def _build_engine(
    import_path: str | None,
    *,
    extract_enabled: bool = False,
    model: str = "",
    base_url: str = "https://api.openai.com/v1",
    api_key: str = "",
    schema_mode: str = "json_schema",
    max_completion_tokens: int | None = None,
    max_tokens_parameter: str = "max_completion_tokens",
    thinking: str | None = None,
) -> _DoppelEngine | None:
    module = _load_doppel_module(import_path)
    if module is None:
        return None
    try:
        from app.integrations.doppel.config import doppel_db_path

        if extract_enabled and not model:
            raise ValueError("DOPPEL_MODEL is required when extraction is enabled")
        client = module.DoppelClient(backend="sqlite", database=doppel_db_path())
        provider = None
        extractor = None
        if extract_enabled:
            provider = module.OpenAICompatibleStructuredOutputModel(
                module.OpenAICompatibleStructuredOutputConfig(
                    model=model,
                    base_url=base_url,
                    schema_mode=schema_mode,
                    max_completion_tokens=max_completion_tokens,
                    max_tokens_parameter=max_tokens_parameter,
                    thinking=thinking,
                ),
                api_key=api_key,
            )
            extractor = module.PersonalMemoryExtractor(
                module.ReferencePersonalMemoryAnalyzer(provider)
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("doppel engine init failed: %s", exc)
        return None
    return _DoppelEngine(
        module,
        client,
        extractor=extractor,
        provider=provider,
    )
