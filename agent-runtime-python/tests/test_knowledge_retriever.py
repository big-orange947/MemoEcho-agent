from __future__ import annotations

import tempfile
from pathlib import Path

import unittest

from app.memory.knowledge_retriever import KnowledgeRetriever
from app.schemas.events import Sender, UnifiedEvent
from app.schemas.profiles import ConversationProfile


class KnowledgeRetrieverTest(unittest.IsolatedAsyncioTestCase):
    async def test_retrieves_relevant_fragment_from_bound_local_markdown(self) -> None:
        """设定集绑定的本地资料应只返回和当前消息有关的片段，而非整篇文档。"""
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "membership.md"
            source.write_text(
                "售后问题请在工作日处理。\n\n网易云会员支持微信付款，月卡价格为十五元。\n\n其他事项请联系管理员。",
                encoding="utf-8",
            )
            event = UnifiedEvent(
                eventId="knowledge-case",
                platform="qq",
                scene="social",
                eventType="message",
                chatType="private",
                chatId="peer-1",
                sender=Sender(id="peer-1", name="peer"),
                text="会员怎么微信付款",
                timestamp="2026-07-12T12:00:00+08:00",
                rawPayload={},
            )
            profile = ConversationProfile(
                id="profile-1",
                name="会员会话",
                chatType="private",
                knowledgeBaseSources=[str(source)],
            )

            results = await KnowledgeRetriever().retrieve(event, profile)

        self.assertEqual(1, len(results))
        self.assertIn("微信付款", results[0]["content"])
        self.assertNotIn("售后问题", results[0]["content"])
