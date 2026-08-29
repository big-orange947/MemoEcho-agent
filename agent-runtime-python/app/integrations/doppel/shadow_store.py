"""Persistent shadow inbox for Doppel evaluation.

Fully isolated from MemoEcho's production data: one SQLite file, never
touches Java event-center / Neo4j / LangGraph checkpoints.  Events are
appended idempotently (INSERT OR IGNORE on the stable event id).

Inbox lifecycle (claim model): a row moves
``pending -> processing -> succeeded | failed_retryable | dead_letter``.
Crash between append and completion leaves the row in ``processing``;
a restart re-queues it as ``pending`` so the pipeline is eventually run.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
import asyncio

import aiosqlite

logger = logging.getLogger(__name__)

STATUS_PENDING = "pending"
STATUS_PROCESSING = "processing"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED_RETRYABLE = "failed_retryable"
STATUS_DEAD_LETTER = "dead_letter"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS shadow_inbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    payload_json TEXT NOT NULL,
    received_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    processed_at TEXT,
    error TEXT
);
CREATE TABLE IF NOT EXISTS shadow_trace (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    stage TEXT NOT NULL,
    detail_json TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_inbox_pending
    ON shadow_inbox (status, received_at);
"""


class ShadowStore:
    """Async SQLite-backed shadow event log with a claimable inbox."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        max_attempts: int = 3,
    ) -> None:
        self._path = str(db_path)
        self._max_attempts = max(int(max_attempts), 1)
        self._db: aiosqlite.Connection | None = None
        # aiosqlite serializes individual statements, not a multi-statement
        # transaction.  Every operation therefore shares one lock so an
        # append/trace/commit can never interleave with claim_next's
        # BEGIN/SELECT/UPDATE/COMMIT sequence on the same connection.
        self._operation_lock: asyncio.Lock | None = None

    def _ensure_lock(self) -> asyncio.Lock:
        if self._operation_lock is None:
            # Create lazily inside the event loop that owns the store.
            self._operation_lock = asyncio.Lock()
        return self._operation_lock

    async def _open_unlocked(self) -> None:
        if self._db is not None:
            return
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._path)
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.executescript(_SCHEMA)
        await self._migrate()
        await self._db.commit()

    async def open(self) -> None:
        """Idempotent open with a lock; safe under concurrent first events."""
        async with self._ensure_lock():
            await self._open_unlocked()

    async def _migrate(self) -> None:
        assert self._db is not None
        columns = {
            row[1]
            for row in await self._db.execute_fetchall(
                "PRAGMA table_info(shadow_inbox)"
            )
        }
        if "status" not in columns:
            await self._db.execute(
                "ALTER TABLE shadow_inbox ADD COLUMN status TEXT NOT NULL DEFAULT 'pending'"
            )
        if "attempts" not in columns:
            await self._db.execute(
                "ALTER TABLE shadow_inbox ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0"
            )

    async def close(self) -> None:
        async with self._ensure_lock():
            if self._db is not None:
                await self._db.close()
                self._db = None

    @property
    def is_open(self) -> bool:
        return self._db is not None

    async def append(self, event_id: str, payload: dict) -> bool | None:
        """Insert idempotently: True=new, False=duplicate, None=storage failure."""
        row = (
            event_id,
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
            datetime.now(UTC).isoformat(),
        )
        async with self._ensure_lock():
            await self._open_unlocked()
            assert self._db is not None
            try:
                cursor = await self._db.execute(
                    "INSERT OR IGNORE INTO shadow_inbox "
                    " (event_id, payload_json, received_at, status, attempts)"
                    " VALUES (?, ?, ?, 'pending', 0)",
                    row,
                )
                await self._db.commit()
                return cursor.rowcount > 0
            except Exception:  # noqa: BLE001 - shadow must never break the main loop
                logger.exception("shadow append failed for %s", event_id)
                return None

    async def requeue_stale(self) -> int:
        """Restart recovery: pending/processing/failed_retryable rows move
        back to pending so the pipeline eventually runs (idempotent replay)."""
        stale_before = datetime.now(UTC).isoformat()
        async with self._ensure_lock():
            await self._open_unlocked()
            assert self._db is not None
            try:
                cursor = await self._db.execute(
                    "UPDATE shadow_inbox SET status = 'pending', error = NULL "
                    "WHERE status IN ('pending', 'processing', 'failed_retryable') "
                    "AND received_at < ?",
                    (stale_before,),
                )
                await self._db.commit()
                return cursor.rowcount or 0
            except Exception:  # noqa: BLE001
                logger.exception("shadow requeue_stale failed")
                return 0

    async def requeue_retryable(self, event_id: str) -> bool:
        """Move one retryable row back to pending after worker-side backoff."""
        async with self._ensure_lock():
            await self._open_unlocked()
            assert self._db is not None
            try:
                cursor = await self._db.execute(
                    "UPDATE shadow_inbox SET status = 'pending' "
                    "WHERE event_id = ? AND status = 'failed_retryable'",
                    (event_id,),
                )
                await self._db.commit()
                return bool(cursor.rowcount)
            except Exception:  # noqa: BLE001
                logger.exception("shadow retry requeue failed for %s", event_id)
                return False

    async def claim_next(self) -> dict | None:
        """Atomically claim one pending row (event_id, payload)."""
        async with self._ensure_lock():
            await self._open_unlocked()
            assert self._db is not None
            try:
                await self._db.execute("BEGIN IMMEDIATE")
                cursor = await self._db.execute(
                    "SELECT id, event_id, payload_json, attempts FROM shadow_inbox "
                    "WHERE status = 'pending' ORDER BY received_at, id LIMIT 1"
                )
                row = await cursor.fetchone()
                if row is None:
                    await self._db.execute("COMMIT")
                    return None
                row_id, event_id, payload_json, attempts = row
                await self._db.execute(
                    "UPDATE shadow_inbox SET status = 'processing', attempts = ? WHERE id = ?",
                    (attempts + 1, row_id),
                )
                await self._db.execute("COMMIT")
                return {
                    "event_id": event_id,
                    "payload": json.loads(payload_json),
                    "attempts": attempts + 1,
                }
            except Exception:  # noqa: BLE001
                try:
                    await self._db.execute("ROLLBACK")
                except Exception:  # noqa: BLE001
                    pass
                logger.exception("shadow claim_next failed")
                return None

    async def complete(
        self,
        event_id: str,
        *,
        succeeded: bool,
        error: str = "",
    ) -> str:
        status = STATUS_SUCCEEDED if succeeded else STATUS_FAILED_RETRYABLE
        async with self._ensure_lock():
            await self._open_unlocked()
            assert self._db is not None
            try:
                if succeeded:
                    await self._db.execute(
                        "UPDATE shadow_inbox SET status = 'succeeded', processed_at = ?, error = NULL "
                        "WHERE event_id = ?",
                        (datetime.now(UTC).isoformat(), event_id),
                    )
                else:
                    cursor = await self._db.execute(
                        "SELECT attempts FROM shadow_inbox WHERE event_id = ?",
                        (event_id,),
                    )
                    row = await cursor.fetchone()
                    attempts = int(row[0]) if row else 0
                    status = (
                        STATUS_DEAD_LETTER
                        if attempts >= self._max_attempts
                        else STATUS_FAILED_RETRYABLE
                    )
                    await self._db.execute(
                        "UPDATE shadow_inbox SET status = ?, processed_at = ?, error = ? "
                        "WHERE event_id = ?",
                        (
                            status,
                            datetime.now(UTC).isoformat(),
                            error[:500] or None,
                            event_id,
                        ),
                    )
                await self._db.commit()
            except Exception:  # noqa: BLE001
                logger.exception("shadow complete failed for %s", event_id)
                return STATUS_FAILED_RETRYABLE
        return status

    async def trace(self, event_id: str, stage: str, detail: dict) -> None:
        async with self._ensure_lock():
            await self._open_unlocked()
            assert self._db is not None
            try:
                await self._db.execute(
                    "INSERT OR REPLACE INTO shadow_trace (event_id, stage, detail_json, recorded_at)"
                    " VALUES (?, ?, ?, ?)",
                    (
                        event_id,
                        stage,
                        json.dumps(detail, ensure_ascii=False, sort_keys=True),
                        datetime.now(UTC).isoformat(),
                    ),
                )
                await self._db.commit()
            except Exception:  # noqa: BLE001
                logger.exception("shadow trace failed for %s", event_id)

    async def counts(self) -> dict[str, int]:
        counts: dict[str, int] = {
            "total": 0,
            "pending": 0,
            "processing": 0,
            "succeeded": 0,
            "failed_retryable": 0,
            "dead_letter": 0,
        }
        async with self._ensure_lock():
            await self._open_unlocked()
            assert self._db is not None
            try:
                async with self._db.execute(
                    "SELECT status, COUNT(*) FROM shadow_inbox GROUP BY status"
                ) as cursor:
                    for status, count in await cursor.fetchall():
                        counts[str(status)] = int(count)
                async with self._db.execute(
                    "SELECT COUNT(*) FROM shadow_inbox"
                ) as cursor:
                    row = await cursor.fetchone()
                counts["total"] = int(row[0]) if row else 0
            except Exception:  # noqa: BLE001
                logger.exception("shadow counts failed")
        return counts

    async def drain(self) -> int:
        counts = await self.counts()
        return counts.get("pending", 0)
