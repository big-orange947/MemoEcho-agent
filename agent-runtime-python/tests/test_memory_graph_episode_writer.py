"""MemoryGraphEpisodeWriter 单元测试：节流合并、actorType 打标、降级。

不依赖真实 Neo4j / LLM：注入 fake graph_service 验证写入行为。
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from app.memory.graph_episode_writer import MemoryGraphEpisodeWriter
from app.schemas.events import Sender, UnifiedEvent


class FakeGraphService:
    def __init__(self, enabled: bool = True) -> None:
        self.is_enabled = enabled
        self.writes: list[dict] = []

    async def write_episode(self, **kwargs):
        self.writes.append(kwargs)
        return {"ok": True}


def _event(
    text: str,
    *,
    event_id: str = "evt-1",
    actor_type: str = "OWNER",
    chat_id: str = "10001",
    platform: str = "qq",
    chat_type: str = "private",
    self_id: str = "3807050597",
    sent_at: str | None = None,
) -> UnifiedEvent:
    return UnifiedEvent(
        eventId=event_id,
        platform=platform,
        eventType="message",
        chatType=chat_type,
        chatId=chat_id,
        selfId=self_id,
        sender=Sender(id="3807050597", name="号主"),
        text=text,
        timestamp=sent_at or datetime.now(timezone.utc).isoformat(),
        sentAt=sent_at,
        actorType=actor_type,
    )


class MemoryGraphEpisodeWriterTest(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_skips_schedule(self) -> None:
        graph = FakeGraphService(enabled=False)
        writer = MemoryGraphEpisodeWriter(graph)
        self.assertFalse(writer.schedule(_event("你好呀")))
        await writer.close()

    async def test_ineligible_events_skipped(self) -> None:
        graph = FakeGraphService(enabled=True)
        writer = MemoryGraphEpisodeWriter(graph)
        self.assertFalse(writer.schedule(_event("h", event_id="e1")))  # 文本过短
        # 平台级排除：构造 desktop 命令事件
        desktop = UnifiedEvent(
            eventId="e2",
            platform="desktop",
            eventType="desktop_command",
            chatType="private",
            chatId="10001",
            selfId="3807050597",
            sender=Sender(id="3807050597", name="号主"),
            text="列出任务",
            timestamp=datetime.now(timezone.utc).isoformat(),
            actorType="OWNER",
        )
        self.assertFalse(writer.schedule(desktop))
        await writer.close()

    async def test_batches_until_message_threshold(self) -> None:
        graph = FakeGraphService(enabled=True)
        writer = MemoryGraphEpisodeWriter(graph, flush_message_count=3, flush_window_seconds=9999)
        base = datetime(2026, 8, 23, 12, 0, 0, tzinfo=timezone.utc)
        for i in range(2):
            await writer._ingest(_event(f"消息{i}", event_id=f"e{i}", sent_at=(base + timedelta(seconds=i)).isoformat()))
        self.assertEqual(len(graph.writes), 0)  # 未达阈值不写入
        await writer._ingest(_event("消息2", event_id="e2", sent_at=(base + timedelta(seconds=2)).isoformat()))
        self.assertEqual(len(graph.writes), 1)
        body = graph.writes[0]["episode_body"]
        self.assertIn("[actorType=OWNER", body)
        self.assertIn("消息0", body)
        self.assertIn("消息2", body)
        self.assertEqual(graph.writes[0]["group_id"], "3807050597:qq:private:10001")
        self.assertIn("eventIds=e0,e1,e2", graph.writes[0]["source_description"])
        await writer.close()

    async def test_window_forces_flush(self) -> None:
        graph = FakeGraphService(enabled=True)
        writer = MemoryGraphEpisodeWriter(graph, flush_message_count=999, flush_window_seconds=60)
        base = datetime(2026, 8, 23, 12, 0, 0, tzinfo=timezone.utc)
        await writer._ingest(_event("首条", event_id="e0", sent_at=base.isoformat()))
        self.assertEqual(len(graph.writes), 0)
        # 模拟 61 秒后新消息到达：应触发时间窗口合并写入
        await writer._ingest(_event("后续", event_id="e1", sent_at=(base + timedelta(seconds=61)).isoformat()))
        self.assertEqual(len(graph.writes), 1)
        await writer.close()

    async def test_overdue_guardian_flushes_stale_buffer(self) -> None:
        graph = FakeGraphService(enabled=True)
        writer = MemoryGraphEpisodeWriter(graph, flush_message_count=999, flush_window_seconds=60)
        base = datetime.now(timezone.utc) - timedelta(seconds=120)
        await writer._ingest(_event("旧消息", event_id="e0", sent_at=base.isoformat()))
        self.assertEqual(len(graph.writes), 0)
        await writer._flush_overdue()
        self.assertEqual(len(graph.writes), 1)
        await writer.close()

    async def test_agent_and_contact_messages_keep_actor_tag(self) -> None:
        graph = FakeGraphService(enabled=True)
        writer = MemoryGraphEpisodeWriter(graph, flush_message_count=2, flush_window_seconds=9999)
        await writer._ingest(_event("号主说", event_id="e0", actor_type="OWNER"))
        await writer._ingest(_event("对方回", event_id="e1", actor_type="CONTACT"))
        body = graph.writes[0]["episode_body"]
        self.assertIn("[actorType=OWNER", body)
        self.assertIn("[actorType=CONTACT", body)
        await writer.close()


if __name__ == "__main__":
    unittest.main()
