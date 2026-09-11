# -*- coding: utf-8 -*-
"""存储保留策略测试: 该删的删掉,不该删的一条都不能动。

这个功能的风险不对称:
  · 少删一点 —— 只是占点磁盘,无所谓;
  · 多删一条 —— 对话记录和长期记忆的原始素材永久消失,无法恢复。

所以测试的重点不在"能删",而在**保护规则**:
  1. 没总结过的消息不能删(攒批是长期记忆的唯一入口,删了就是永久丢失);
  2. 有进行中目标的会话整个跳过(任务上下文不能动);
  3. 每个会话始终留足最近 N 条;
  4. 已触发/未到期的定时唤醒,只删前者;
  5. 没跑完的调度任务不能删(还要查结果)。

不联网、不依赖真实 LLM。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app import retention
from app.db import get_connection

NOW = datetime(2026, 9, 11, 12, 0, 0, tzinfo=timezone.utc)


def _days_ago(days: float) -> str:
    return (NOW - timedelta(days=days)).astimezone(timezone.utc).isoformat()


@pytest.fixture()
def env(temp_data_dir, monkeypatch):
    """建表 + 默认策略(消息 90 天、其余 30 天、每会话保底 200 条)。"""
    monkeypatch.setenv("MEMO_ECHO_RETENTION_ENABLED", "true")
    monkeypatch.setenv("MEMO_ECHO_MESSAGE_RETENTION_DAYS", "90")
    monkeypatch.setenv("MEMO_ECHO_MESSAGE_KEEP_MIN", "200")
    monkeypatch.setenv("MEMO_ECHO_EVENT_RETENTION_DAYS", "30")
    monkeypatch.setenv("MEMO_ECHO_DISPATCH_RETENTION_DAYS", "30")
    monkeypatch.setenv("MEMO_ECHO_SCHEDULE_RETENTION_DAYS", "30")
    monkeypatch.setenv("MEMO_ECHO_REPORT_RETENTION_DAYS", "30")
    monkeypatch.setenv("MEMO_ECHO_CHECKPOINT_RETENTION_DAYS", "0")  # 默认不碰 checkpoint

    from app import config as config_module

    monkeypatch.setattr(config_module, "_settings", None, raising=False)

    from app.db import init_db

    init_db()
    return None


def _make_conversation(*, monitor: int = 0, external_id: str = "10001", updated_days_ago: float = 0) -> str:
    from app.services import conversations as conversations_service

    conversation_id = conversations_service.ensure_conversation("qq", "group", external_id)
    conn = get_connection()
    conn.execute(
        "UPDATE conversations SET monitor=?, updated_at=? WHERE id=?",
        (monitor, _days_ago(updated_days_ago), conversation_id),
    )
    conn.commit()
    return conversation_id


def _add_messages(conversation_id: str, count: int, *, days_ago: float, prefix: str = "旧消息") -> None:
    """塞进 count 条 count 天前的消息。"""
    conn = get_connection()
    for index in range(count):
        conn.execute(
            "INSERT INTO messages (id, conversation_id, role, source, content, created_at)"
            " VALUES (?, ?, 'user', 'inbound', ?, ?)",
            (f"{conversation_id}-{prefix}-{index}", conversation_id, f"{prefix}{index}", _days_ago(days_ago)),
        )
    conn.commit()


def _set_watermark(conversation_id: str, days_ago: float) -> None:
    """设置攒批水位线(表示该时间之前的消息已进长期记忆)。"""
    conn = get_connection()
    conn.execute(
        "INSERT OR REPLACE INTO memory_batches (conversation_id, last_message_at, last_status)"
        " VALUES (?, ?, 'ok')",
        (conversation_id, _days_ago(days_ago)),
    )
    conn.commit()


def _message_count(conversation_id: str) -> int:
    row = get_connection().execute(
        "SELECT COUNT(*) AS n FROM messages WHERE conversation_id=?", (conversation_id,)
    ).fetchone()
    return int(row["n"] or 0)


# ---------------------------------------------------------------------------
# 消息保护规则(最重要)
# ---------------------------------------------------------------------------
class TestMessageProtection:
    @pytest.mark.asyncio
    async def test_never_summarized_is_untouched(self, env):
        """从未总结过(没有水位线)⇒ 一条都不删。

        这是最重要的一条: 攒批总结读的就是 messages 表,
        删掉未总结的消息 = 那段记忆永久消失。
        """
        conversation_id = _make_conversation(monitor=1)
        _add_messages(conversation_id, 50, days_ago=200)   # 远超保留期

        result = await retention.apply(now=NOW)

        assert _message_count(conversation_id) == 50
        assert result["deleted"]["messages"] == 0
        assert any("水位线" in item["reason"] for item in result["skipped_conversations"])

    @pytest.mark.asyncio
    async def test_summarized_old_messages_are_removed(self, env, monkeypatch):
        """已总结(水位线之后无遗留)且超过保留期 ⇒ 可以删。

        保底条数设为 0: 这里要验的是"水位线判定",不是保底规则。
        """
        monkeypatch.setenv("MEMO_ECHO_MESSAGE_KEEP_MIN", "0")
        from app import config as config_module

        config_module._settings = None

        conversation_id = _make_conversation(monitor=1)
        _add_messages(conversation_id, 50, days_ago=200)
        _set_watermark(conversation_id, days_ago=100)   # 100 天前就总结完了

        result = await retention.apply(now=NOW)

        assert result["deleted"]["messages"] == 50
        assert _message_count(conversation_id) == 0

    @pytest.mark.asyncio
    async def test_unsummarized_part_is_protected(self, env, monkeypatch):
        """水位线之后(还没总结)的消息即使很旧也要留着。

        场景: 某个会话积压很久没总结 —— 恰恰说明攒批卡住了,
        这时候删掉积压 = 把"还没进记忆的东西"抹掉。
        """
        monkeypatch.setenv("MEMO_ECHO_MESSAGE_KEEP_MIN", "0")
        from app import config as config_module

        config_module._settings = None

        conversation_id = _make_conversation(monitor=1)
        _add_messages(conversation_id, 30, days_ago=300, prefix="早已总结")
        _add_messages(conversation_id, 20, days_ago=150, prefix="还没总结")
        _set_watermark(conversation_id, days_ago=200)   # 只总结到 200 天前

        result = await retention.apply(now=NOW)

        remaining = {
            row["content"]
            for row in get_connection().execute(
                "SELECT content FROM messages WHERE conversation_id=?", (conversation_id,)
            ).fetchall()
        }
        assert len(remaining) == 20, f"未总结的消息被删了: {len(remaining)} 条"
        assert all("还没总结" in item for item in remaining)
        assert result["deleted"]["messages"] == 30

    @pytest.mark.asyncio
    async def test_active_goal_conversation_skipped(self, env):
        """有进行中目标的会话整个跳过 —— 任务上下文不能动。"""
        from app.services import goals as goals_service

        conversation_id = _make_conversation(monitor=1)
        _add_messages(conversation_id, 50, days_ago=200)
        _set_watermark(conversation_id, days_ago=100)
        goals_service.create_goal(conversation_id, "帮约 km 打游戏")

        result = await retention.apply(now=NOW)

        assert _message_count(conversation_id) == 50
        assert any("进行中的目标" in item["reason"] for item in result["skipped_conversations"])

    @pytest.mark.asyncio
    async def test_keep_min_floor_respected(self, env, monkeypatch):
        """每个会话始终保留最近 N 条(上下文安全垫)。"""
        monkeypatch.setenv("MEMO_ECHO_MESSAGE_KEEP_MIN", "10")
        from app import config as config_module

        config_module._settings = None

        conversation_id = _make_conversation(monitor=1)
        _add_messages(conversation_id, 15, days_ago=300)
        _set_watermark(conversation_id, days_ago=250)   # 全部已总结

        result = await retention.apply(now=NOW)

        assert result["deleted"]["messages"] == 5, "应只删到只剩保底条数"
        assert _message_count(conversation_id) == 10

    @pytest.mark.asyncio
    async def test_unmonitored_conversation_still_cleaned(self, env, monkeypatch):
        """未监视会话: 没有攒批流程,按时间正常清理(它本来就不进记忆)。"""
        monkeypatch.setenv("MEMO_ECHO_MESSAGE_KEEP_MIN", "0")
        from app import config as config_module

        config_module._settings = None

        conversation_id = _make_conversation(monitor=0, external_id="20002")
        _add_messages(conversation_id, 40, days_ago=200)

        result = await retention.apply(now=NOW)

        assert result["deleted"]["messages"] == 40

    @pytest.mark.asyncio
    async def test_recent_messages_untouched(self, env):
        """保留期内的消息一律不动。"""
        conversation_id = _make_conversation(monitor=1)
        _add_messages(conversation_id, 30, days_ago=10)

        result = await retention.apply(now=NOW)

        assert result["deleted"]["messages"] == 0
        assert _message_count(conversation_id) == 30

    @pytest.mark.asyncio
    async def test_days_zero_disables_message_cleanup(self, env, monkeypatch):
        """设为 0 = 永久保留(给"我就要留着"的用户一个出口)。"""
        monkeypatch.setenv("MEMO_ECHO_MESSAGE_RETENTION_DAYS", "0")
        from app import config as config_module

        config_module._settings = None

        conversation_id = _make_conversation(monitor=1)
        _add_messages(conversation_id, 50, days_ago=400)
        _set_watermark(conversation_id, days_ago=300)

        result = await retention.apply(now=NOW)
        assert result["deleted"]["messages"] == 0
        assert _message_count(conversation_id) == 50


# ---------------------------------------------------------------------------
# 其余各表
# ---------------------------------------------------------------------------
class TestOtherTables:
    @pytest.mark.asyncio
    async def test_events_cleaned_by_age(self, env):
        conn = get_connection()
        conn.execute(
            "INSERT INTO events (id, event_type, created_at) VALUES ('old-evt', 'message', ?)",
            (_days_ago(60),),
        )
        conn.execute(
            "INSERT INTO events (id, event_type, created_at) VALUES ('new-evt', 'message', ?)",
            (_days_ago(1),),
        )
        conn.commit()

        result = await retention.apply(now=NOW)

        assert result["deleted"]["events"] == 1
        remaining = {row["id"] for row in conn.execute("SELECT id FROM events").fetchall()}
        assert remaining == {"new-evt"}

    @pytest.mark.asyncio
    async def test_unfinished_dispatch_kept(self, env):
        """没跑完的任务不能删 —— 还要查结果/重试。"""
        conn = get_connection()
        now_iso = _days_ago(0)
        for task_id, status in (
            ("done-old", "done"),
            ("running-old", "running"),
            ("accepted-old", "accepted"),
            ("failed-old", "failed"),
        ):
            conn.execute(
                "INSERT INTO dispatches (task_id, kind, status, created_at, updated_at)"
                " VALUES (?, 'task', ?, ?, ?)",
                (task_id, status, _days_ago(90), _days_ago(90)),
            )
        conn.commit()

        result = await retention.apply(now=NOW)

        assert result["deleted"]["dispatches"] == 2   # done + failed
        remaining = {row["task_id"] for row in conn.execute("SELECT task_id FROM dispatches").fetchall()}
        assert remaining == {"running-old", "accepted-old"}
        assert now_iso  # 仅避免未使用告警

    @pytest.mark.asyncio
    async def test_pending_schedule_kept(self, env):
        """未到点的定时唤醒永远不删 —— 删了就永远不会被唤醒。"""
        conversation_id = _make_conversation(monitor=0, external_id="30003")
        conn = get_connection()
        conn.execute(
            "INSERT INTO scheduled_events (id, conversation_id, due_at, status, created_at)"
            " VALUES ('fired-1', ?, ?, 'fired', ?)",
            (conversation_id, _days_ago(1), _days_ago(60)),
        )
        conn.execute(
            "INSERT INTO scheduled_events (id, conversation_id, due_at, status, created_at)"
            " VALUES ('pending-1', ?, ?, 'pending', ?)",
            (conversation_id, _days_ago(-1), _days_ago(60)),
        )
        conn.execute(
            "INSERT INTO scheduled_events (id, conversation_id, due_at, status, created_at)"
            " VALUES ('fired-new', ?, ?, 'fired', ?)",
            (conversation_id, _days_ago(1), _days_ago(1)),
        )
        conn.commit()

        result = await retention.apply(now=NOW)

        assert result["deleted"]["scheduled_events"] == 1
        remaining = {row["id"] for row in conn.execute("SELECT id FROM scheduled_events").fetchall()}
        assert remaining == {"pending-1", "fired-new"}

    @pytest.mark.asyncio
    async def test_pending_report_kept(self, env):
        """还没被消费的上报不能删(那是待办,不是历史)。"""
        from app import reports as reports_service

        reports_service.enqueue(
            lane=reports_service.LANE_URGENT,
            conversation_id="conv-y",
            message_ids=["m1"],
            payload={"summary": "待处理"},
            dedup_key="pending-report",
            status=reports_service.STATUS_PENDING,
        )
        reports_service.enqueue(
            lane=reports_service.LANE_NORMAL,
            conversation_id="conv-y",
            message_ids=["m2"],
            payload={"summary": "已处理"},
            dedup_key="acked-report",
            status=reports_service.STATUS_PENDING,
        )
        # 把第二条标成已 ack 并做旧
        conn = get_connection()
        conn.execute(
            "UPDATE report_queue SET status='acked', updated_at=? WHERE dedup_key='acked-report'",
            (_days_ago(60),),
        )
        conn.commit()

        result = await retention.apply(now=NOW)

        assert result["deleted"]["report_queue"] == 1
        remaining = {row["dedup_key"] for row in conn.execute("SELECT dedup_key FROM report_queue").fetchall()}
        assert remaining == {"pending-report"}


# ---------------------------------------------------------------------------
# 开关与盘点
# ---------------------------------------------------------------------------
class TestSwitchAndPlan:
    @pytest.mark.asyncio
    async def test_disabled_does_nothing(self, env, monkeypatch):
        monkeypatch.setenv("MEMO_ECHO_RETENTION_ENABLED", "false")
        from app import config as config_module

        config_module._settings = None

        conversation_id = _make_conversation(monitor=1)
        _add_messages(conversation_id, 50, days_ago=300)
        _set_watermark(conversation_id, days_ago=200)

        result = await retention.apply(now=NOW)

        assert result["enabled"] is False
        assert _message_count(conversation_id) == 50

    @pytest.mark.asyncio
    async def test_plan_is_pure_dry_run(self, env, monkeypatch):
        """盘点不能删任何东西 —— 它只是"告诉我会发生什么"。"""
        monkeypatch.setenv("MEMO_ECHO_MESSAGE_KEEP_MIN", "0")
        from app import config as config_module

        config_module._settings = None

        conversation_id = _make_conversation(monitor=1)
        _add_messages(conversation_id, 50, days_ago=200)
        _set_watermark(conversation_id, days_ago=100)

        plan = await retention.plan(now=NOW)

        assert plan["messages"]["candidates"] == 50
        assert _message_count(conversation_id) == 50   # 一条没少

    @pytest.mark.asyncio
    async def test_plan_reports_skip_reasons(self, env):
        """盘点要说清"为什么没删",而不是只给一个数字。"""
        conversation_id = _make_conversation(monitor=1)
        _add_messages(conversation_id, 50, days_ago=200)

        plan = await retention.plan(now=NOW)

        assert plan["messages"]["candidates"] == 0
        assert plan["messages"]["skipped"], "应说明跳过原因"
        assert conversation_id in str(plan["messages"]["skipped"])

    def test_storage_overview(self, env):
        stats = retention.storage_overview()
        assert "memo-echo.db" in stats["files"]
        assert stats["rows"]["conversations"] >= 0
        assert stats["total_bytes"] >= 0
        assert "B" in stats["total_human"] or "K" in stats["total_human"]


# ---------------------------------------------------------------------------
# 调度接线
# ---------------------------------------------------------------------------
class TestSchedulerHook:
    @pytest.mark.asyncio
    async def test_throttled_and_errors_swallowed(self, env, monkeypatch):
        """清理按间隔节流,且失败不影响调度循环。"""
        from app import scheduler as scheduler_module

        scheduler = scheduler_module.Scheduler()
        calls: list[int] = []

        async def fake_run_retention() -> None:
            calls.append(1)
            raise RuntimeError("模拟清理失败")

        # 图实例缺失不该让清理炸掉(它内部 try/except 吞掉)
        async def failing_apply(**kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(retention, "apply", failing_apply)

        await scheduler._maybe_run_retention()
        assert scheduler._retention_task is not None
        try:
            await scheduler._retention_task
        except Exception as exc:  # noqa: BLE001
            pytest.fail(f"清理任务抛出了异常,会打断调度循环: {exc}")

        # 第二次调用应被节流跳过(间隔 24 小时)
        monkeypatch.setattr(scheduler_module, "get_settings", lambda: type("S", (), {"retention_interval_hours": 24})())
        first_task = scheduler._retention_task
        await scheduler._maybe_run_retention()
        assert scheduler._retention_task is first_task, "未按间隔节流"
        assert calls == []
