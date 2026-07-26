from __future__ import annotations

import asyncio
import os
import sqlite3
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.schemas.results import OrchestratorResult


class EventExecutionRegistry:
    """为 Runtime 事件处理提供进程内合并和 SQLite 结果幂等缓存。"""

    def __init__(self, database_path: str | Path | None = None, retention_days: int = 7) -> None:
        """初始化执行注册表，并创建保存已完成事件结果的本地数据库。"""
        configured_path = database_path or os.getenv("MEMO_ECHO_RUNTIME_STATE_DB")
        default_path = Path(__file__).resolve().parents[2] / "data" / "runtime-executions.sqlite3"
        self.database_path = Path(configured_path).expanduser() if configured_path else default_path
        self.retention_days = max(retention_days, 1)
        self._inflight: dict[str, asyncio.Task[OrchestratorResult]] = {}
        self._lock = asyncio.Lock()
        self._initialize_database()
        self._delete_expired_results()

    async def execute(
        self,
        event_id: str,
        handler: Callable[[], Awaitable[OrchestratorResult]],
    ) -> tuple[OrchestratorResult, bool]:
        """执行一次事件；重复事件复用缓存或正在执行的任务，不再次触发平台回写。"""
        cached_result = await asyncio.to_thread(self._load_result, event_id)
        if cached_result is not None:
            return cached_result, True

        async with self._lock:
            existing_task = self._inflight.get(event_id)
            if existing_task is not None:
                task = existing_task
                reused = True
            else:
                task = asyncio.create_task(self._run_and_store(event_id, handler))
                self._inflight[event_id] = task
                reused = False

        # shield 保证 HTTP 客户端提前断开时，已经开始的 Agent 执行仍可完成并保存结果。
        return await asyncio.shield(task), reused

    async def _run_and_store(
        self,
        event_id: str,
        handler: Callable[[], Awaitable[OrchestratorResult]],
    ) -> OrchestratorResult:
        """运行真正的 Orchestrator 调用，成功后持久化结果并清理进行中标记。"""
        try:
            result = await handler()
            await asyncio.to_thread(self._store_result, event_id, result)
            return result
        finally:
            async with self._lock:
                current_task = self._inflight.get(event_id)
                if current_task is asyncio.current_task():
                    self._inflight.pop(event_id, None)

    def _initialize_database(self) -> None:
        """创建 SQLite 文件和幂等结果表，避免要求用户额外启动基础设施。"""
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS runtime_event_execution (
                    event_id TEXT PRIMARY KEY,
                    result_json TEXT NOT NULL,
                    completed_at TEXT NOT NULL
                )
                """
            )
            connection.commit()

    def _load_result(self, event_id: str) -> OrchestratorResult | None:
        """按 eventId 读取已完成结果，反序列化失败时删除损坏缓存并允许重新执行。"""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT result_json FROM runtime_event_execution WHERE event_id = ?",
                (event_id,),
            ).fetchone()
            if row is None:
                return None
            try:
                return OrchestratorResult.model_validate_json(row[0])
            except ValueError:
                connection.execute(
                    "DELETE FROM runtime_event_execution WHERE event_id = ?",
                    (event_id,),
                )
                connection.commit()
                return None

    def _store_result(self, event_id: str, result: OrchestratorResult) -> None:
        """原子覆盖指定事件的最终结果，使服务重启后仍能识别重复请求。"""
        completed_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO runtime_event_execution (event_id, result_json, completed_at)
                VALUES (?, ?, ?)
                ON CONFLICT(event_id) DO UPDATE SET
                    result_json = excluded.result_json,
                    completed_at = excluded.completed_at
                """,
                (event_id, result.model_dump_json(by_alias=True), completed_at),
            )
            connection.commit()

    def _delete_expired_results(self) -> None:
        """删除超过保留期限的结果，防止本地幂等数据库无限增长。"""
        expires_before = datetime.now(timezone.utc) - timedelta(days=self.retention_days)
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM runtime_event_execution WHERE completed_at < ?",
                (expires_before.isoformat(),),
            )
            connection.commit()

    def _connect(self) -> sqlite3.Connection:
        """创建一次短生命周期 SQLite 连接，允许后台线程安全地并发读写。"""
        return sqlite3.connect(self.database_path, timeout=5)
