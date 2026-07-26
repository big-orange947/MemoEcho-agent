from __future__ import annotations

import asyncio

from app.schemas.results import OrchestratorResult
from app.services.event_execution_registry import EventExecutionRegistry


def _result(summary: str) -> OrchestratorResult:
    """构造测试使用的最小 Orchestrator 结果。"""
    return OrchestratorResult(
        execution_id="execution-1",
        status="completed",
        route="social_reply",
        summary=summary,
        results=[],
        final_reply="收到",
        write_back_actions=["qq_write_back_sent:test"],
    )


def test_should_reuse_persisted_result_after_registry_recreated(tmp_path) -> None:
    """验证 Runtime 重建后仍会复用同一 eventId 的持久化结果。"""
    async def scenario() -> None:
        """运行持久化缓存重建场景。"""
        database_path = tmp_path / "runtime.sqlite3"
        calls = 0

        async def handler() -> OrchestratorResult:
            """记录真正执行次数，确保缓存命中后不会再次调用。"""
            nonlocal calls
            calls += 1
            return _result("第一次执行")

        first_registry = EventExecutionRegistry(database_path)
        first_result, first_reused = await first_registry.execute("event-1", handler)
        second_registry = EventExecutionRegistry(database_path)
        second_result, second_reused = await second_registry.execute("event-1", handler)

        assert first_reused is False
        assert second_reused is True
        assert first_result.summary == second_result.summary == "第一次执行"
        assert calls == 1

    asyncio.run(scenario())


def test_should_merge_concurrent_requests_for_same_event(tmp_path) -> None:
    """验证两个并发重复请求只执行一次处理函数，并共同取得相同结果。"""
    async def scenario() -> None:
        """运行两个请求同时处理相同事件的场景。"""
        registry = EventExecutionRegistry(tmp_path / "runtime.sqlite3")
        started = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def handler() -> OrchestratorResult:
            """暂停第一次调用，为第二个请求制造并发重入窗口。"""
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return _result("并发执行")

        first_task = asyncio.create_task(registry.execute("event-2", handler))
        await started.wait()
        second_task = asyncio.create_task(registry.execute("event-2", handler))
        await asyncio.sleep(0)
        release.set()
        first_result, second_result = await asyncio.gather(first_task, second_task)

        assert first_result[0].summary == second_result[0].summary == "并发执行"
        assert {first_result[1], second_result[1]} == {False, True}
        assert calls == 1

    asyncio.run(scenario())


def test_should_not_cache_failed_execution(tmp_path) -> None:
    """验证异常执行不会形成永久失败缓存，后续重试仍可真正运行。"""
    async def scenario() -> None:
        """运行首次失败后再次执行成功的场景。"""
        registry = EventExecutionRegistry(tmp_path / "runtime.sqlite3")
        calls = 0

        async def handler() -> OrchestratorResult:
            """第一次抛错，第二次返回成功结果。"""
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("temporary failure")
            return _result("重试成功")

        try:
            await registry.execute("event-3", handler)
            raise AssertionError("第一次执行应该抛出异常")
        except RuntimeError as exception:
            assert str(exception) == "temporary failure"

        result, reused = await registry.execute("event-3", handler)

        assert result.summary == "重试成功"
        assert reused is False
        assert calls == 2

    asyncio.run(scenario())
