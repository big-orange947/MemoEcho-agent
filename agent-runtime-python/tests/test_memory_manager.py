from __future__ import annotations

import unittest

from app.memory.manager import MemoryManager
from app.memory.context_compressor import HistoryContextCompressor
from app.schemas.events import Sender, UnifiedEvent
from app.schemas.memories import VerifiedMemory
from app.schemas.profiles import ConversationProfile, ConversationProfileMatchResult


class HistoryEventCenterClient:
    """为历史上下文测试提供同时包含本人和对方消息的稳定数据源。"""

    def __init__(self) -> None:
        """保存最近一次查询参数，便于断言 Skill 是否申请了足够大的上下文窗口。"""
        self.last_kwargs: dict = {}

    async def list_conversation_messages(self, **kwargs):
        """返回按时间倒序排列的近期消息。"""
        self.last_kwargs = dict(kwargs)
        return [
            {
                "eventId": "self-001",
                "senderId": "",
                "senderName": "freeze",
                "text": "去啊",
                "timestamp": "2026-07-13T08:02:00Z",
                "messageOrigin": "USER_MANUAL",
            },
            {
                "eventId": "peer-001",
                "senderId": "friend-001",
                "senderName": "小明",
                "text": "下午还去打球吗",
                "timestamp": "2026-07-13T08:01:00Z",
                "messageOrigin": "EXTERNAL",
                "processingStatus": "WAITING_REVIEW",
                "needHumanConfirmation": True,
                "writeBackStatus": "PENDING",
            },
        ]

    @staticmethod
    def resolve_event_user_id(_event):
        return "freeze"


class DuplicateEchoEventCenterClient:
    """模拟 Agent 发送后被 NapCat 和 Event Center 各记录一次的同文回显。"""

    async def list_conversation_messages(self, **_kwargs):
        """返回两条短时间同文己方消息和一条对方消息。"""
        return [
            {
                "eventId": "self-echo-new",
                "senderId": "3969785168",
                "senderName": "freeze",
                "text": "先说下你的选科",
                "timestamp": "2026-07-15T16:33:10+08:00",
                "messageOrigin": "AGENT_AUTO",
            },
            {
                "eventId": "self-echo-old",
                "senderId": "3969785168",
                "senderName": "freeze",
                "text": "先说下你的选科",
                "timestamp": "2026-07-15T16:33:06+08:00",
                "messageOrigin": "EXTERNAL",
            },
            {
                "eventId": "peer-answer",
                "senderId": "friend-001",
                "senderName": "小明",
                "text": "物化生",
                "timestamp": "2026-07-15T16:33:01+08:00",
                "messageOrigin": "EXTERNAL",
            },
        ]

    @staticmethod
    def resolve_event_user_id(_event):
        """返回测试用户 ID。"""
        return "freeze"


class StaleHistoryEventCenterClient:
    """模拟两小时前已经结束的会话，其中还包含代理生成的旧状态。"""

    async def list_conversation_messages(self, **_kwargs):
        return [
            {
                "eventId": "agent-old-001",
                "senderId": "3969785168",
                "senderName": "我",
                "text": "正刷手机呢",
                "timestamp": "2026-07-13T17:18:54+08:00",
                "messageOrigin": "AGENT_AUTO",
            },
            {
                "eventId": "peer-old-001",
                "senderId": "friend-001",
                "senderName": "小明",
                "text": "你好",
                "timestamp": "2026-07-13T17:18:40+08:00",
                "messageOrigin": "EXTERNAL",
            },
        ]

    @staticmethod
    def resolve_event_user_id(_event):
        return "freeze"


class UnifiedIdentityEventCenterClient:
    """返回乱序、重复且 senderId 故意错误的消息，验证统一身份字段是唯一可信来源。"""

    async def list_conversation_messages(self, **_kwargs):
        """模拟延迟 Webhook、同一 clientMessageId 重复回显和用户主动发送同文消息。"""
        return [
            {
                "eventId": "owner-repeat",
                "senderId": "friend-001",
                "senderName": "freeze",
                "text": "好的",
                "timestamp": "2026-07-13T08:04:00Z",
                "messageOrigin": "USER_MANUAL",
                "actorType": "OWNER",
                "clientMessageId": "owner-repeat-001",
                "sequence": 4,
            },
            {
                "eventId": "contact-old",
                "senderId": "3969785168",
                "senderName": "小明",
                "text": "下午见",
                "timestamp": "2026-07-13T08:01:00Z",
                "messageOrigin": "EXTERNAL",
                "actorType": "CONTACT",
                "platformMessageId": "platform-contact-001",
                "sequence": 1,
            },
            {
                "eventId": "agent-echo-duplicate",
                "senderId": "3969785168",
                "senderName": "freeze",
                "text": "好的",
                "timestamp": "2026-07-13T08:03:01Z",
                "messageOrigin": "AGENT_AUTO",
                "actorType": "AGENT",
                "clientMessageId": "runtime-message-001",
                "sequence": 3,
            },
            {
                "eventId": "owner-middle",
                "senderId": "friend-001",
                "senderName": "freeze",
                "text": "三点可以",
                "timestamp": "2026-07-13T08:02:00Z",
                "messageOrigin": "USER_MANUAL",
                "actorType": "OWNER",
                "platformMessageId": "platform-owner-001",
                "sequence": 2,
            },
            {
                "eventId": "agent-echo-original",
                "senderId": "3969785168",
                "senderName": "freeze",
                "text": "好的",
                "timestamp": "2026-07-13T08:03:00Z",
                "messageOrigin": "AGENT_AUTO",
                "actorType": "AGENT",
                "clientMessageId": "runtime-message-001",
                "sequence": 3,
            },
        ]

    @staticmethod
    def resolve_event_user_id(_event):
        """返回测试用户 ID。"""
        return "freeze"


class VerifiedMemoryEventCenterClient:
    """模拟 Event Center 只返回已确认且仍有效的长期记忆。"""

    async def list_verified_memories(self, _event):
        """返回一条带来源引用的会话级已确认记忆。"""
        return [
            VerifiedMemory(
                id="memory-verified-001",
                subject="对方",
                predicate="常用称呼",
                value="小明",
                scopeType="CONVERSATION",
                platform="qq",
                chatType="private",
                chatId="friend-001",
                sourceEventIds=["event-owner-001"],
                sourceActorType="OWNER",
                factAuthority="human_self",
                confidence=0.95,
                status="VERIFIED",
            )
        ]


class BrokenVerifiedMemoryEventCenterClient:
    """模拟长期记忆服务暂时不可用，验证 Runtime 的容错边界。"""

    async def list_verified_memories(self, _event):
        """抛出服务异常，调用方应降级为空记忆而不是中断回复。"""
        raise RuntimeError("event center unavailable")


class InternalControlHistoryEventCenterClient:
    """模拟历史中混入桌面委托启动指令，验证控制面数据不会污染聊天上下文。"""

    async def list_conversation_messages(self, **_kwargs):
        """同时返回内部控制事件和真实联系人消息。"""
        return [
            {
                "eventId": "delegated:start:task-1",
                "eventType": "delegated_task_started",
                "senderId": "freeze",
                "senderName": "freeze",
                "text": "帮我和km约一下明天下午的课程",
                "timestamp": "2026-07-22T10:00:00+08:00",
                "messageOrigin": "INTERNAL",
            },
            {
                "eventId": "peer-appointment",
                "eventType": "message",
                "senderId": "friend-001",
                "senderName": "小明",
                "text": "明天下午可以",
                "timestamp": "2026-07-22T10:01:00+08:00",
                "messageOrigin": "EXTERNAL",
            },
        ]

    @staticmethod
    def resolve_event_user_id(_event):
        """返回当前账号主人，供消息双方身份归一化使用。"""
        return "freeze"


class MemoryManagerTest(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _build_private_event() -> UnifiedEvent:
        """构造长期记忆测试共用的 QQ 私聊事件。"""
        return UnifiedEvent(
            eventId="current-memory-001",
            platform="qq",
            scene="life",
            eventType="message",
            chatType="private",
            chatId="friend-001",
            selfId="3969785168",
            sender=Sender(id="friend-001", name="小明", role=None),
            text="下午还去吗",
            timestamp="2026-07-17T10:00:00+08:00",
            rawPayload={},
        )

    async def test_should_load_only_service_verified_memories(self) -> None:
        """MemoryManager 不自行提升候选可信度，只转交 Event Center 已确认的记录。"""
        manager = MemoryManager(VerifiedMemoryEventCenterClient())

        memories = await manager.build_verified_memories(self._build_private_event())

        self.assertEqual(1, len(memories))
        self.assertEqual("memory-verified-001", memories[0].id)
        self.assertEqual("VERIFIED", memories[0].status)
        self.assertEqual("human_self", memories[0].fact_authority)

    async def test_should_ignore_verified_memory_service_failure(self) -> None:
        """长期记忆是增强信息，读取失败时不得阻塞基础聊天链路。"""
        manager = MemoryManager(BrokenVerifiedMemoryEventCenterClient())

        memories = await manager.build_verified_memories(self._build_private_event())

        self.assertEqual([], memories)

    async def test_should_keep_both_sides_of_private_history(self) -> None:
        """验证本人消息既可通过 selfId，也可通过自身消息来源标记进入上下文。"""
        manager = MemoryManager(HistoryEventCenterClient())
        event = UnifiedEvent(
            eventId="current-001",
            platform="qq",
            scene="life",
            eventType="message",
            chatType="private",
            chatId="friend-001",
            selfId="3969785168",
            sender=Sender(id="friend-001", name="小明", role=None),
            text="那就下午见",
            attachments=[],
            mentions=[],
            timestamp="2026-07-13T08:03:00Z",
            rawPayload={},
        )
        profile_match = ConversationProfileMatchResult(
            matched=True,
            active=True,
            profile=ConversationProfile(
                id="profile-001",
                name="私聊上下文",
                privateHistoryEnabled=True,
            ),
        )

        history = await manager.build_history_context(event, profile_match)

        self.assertEqual(["peer", "self"], [item["role"] for item in history])
        self.assertEqual("下午还去打球吗", history[0]["text"])
        self.assertEqual("去啊", history[1]["text"])
        self.assertEqual("human_self", history[1]["factAuthority"])
        self.assertEqual("WAITING_REVIEW", history[0]["processingStatus"])
        self.assertTrue(history[0]["needHumanConfirmation"])
        self.assertEqual("PENDING", history[0]["writeBackStatus"])

    async def test_should_exclude_internal_delegated_command_from_history(self) -> None:
        """工作台委托是控制指令，不能作为账号主人已经说过的话注入 SocialAgent。"""
        manager = MemoryManager(InternalControlHistoryEventCenterClient())
        profile_match = ConversationProfileMatchResult(
            matched=True,
            active=True,
            profile=ConversationProfile(
                id="profile-control-filter",
                name="控制面隔离",
                privateHistoryEnabled=True,
            ),
        )

        history = await manager.build_history_context(self._build_private_event(), profile_match)

        self.assertEqual(["明天下午可以"], [item["text"] for item in history])
        self.assertEqual(["peer"], [item["role"] for item in history])
        self.assertNotIn("帮我和km约一下明天下午的课程", str(history))

    async def test_should_keep_active_session_when_archived_history_is_disabled(self) -> None:
        """关闭归档历史只隔离旧会话，不能让当前连续私聊每收到一条消息就失忆。"""
        client = HistoryEventCenterClient()
        manager = MemoryManager(client)
        event = UnifiedEvent(
            eventId="current-disabled-history",
            platform="qq",
            scene="life",
            eventType="message",
            chatType="private",
            chatId="friend-001",
            selfId="3969785168",
            sender=Sender(id="friend-001", name="小明", role=None),
            text="物化生",
            attachments=[],
            mentions=[],
            timestamp="2026-07-13T08:03:00Z",
            rawPayload={},
        )
        profile_match = ConversationProfileMatchResult(
            matched=True,
            active=True,
            profile=ConversationProfile(
                id="profile-active-session",
                name="只保留当前会话",
                privateHistoryEnabled=False,
                historyMaxMessages=8,
            ),
        )

        history = await manager.build_history_context(event, profile_match)

        self.assertEqual(["peer", "self"], [item["role"] for item in history])
        self.assertEqual(33, client.last_kwargs["limit"])

    async def test_skill_should_expand_history_window_without_enabling_archived_history(self) -> None:
        """Skill 多轮收集资料时扩大当前会话窗口，但仍不越过会话间隔读取旧聊天。"""
        client = HistoryEventCenterClient()
        manager = MemoryManager(client)
        event = UnifiedEvent(
            eventId="current-skill-window",
            platform="qq",
            scene="life",
            eventType="message",
            chatType="private",
            chatId="friend-001",
            selfId="3969785168",
            sender=Sender(id="friend-001", name="小明", role=None),
            text="物化生",
            timestamp="2026-07-13T08:03:00Z",
            rawPayload={},
        )
        profile_match = ConversationProfileMatchResult(
            matched=True,
            active=True,
            profile=ConversationProfile(
                id="profile-skill-window",
                name="Skill 当前上下文",
                privateHistoryEnabled=False,
                historyMaxMessages=8,
            ),
        )

        history = await manager.build_history_context(
            event,
            profile_match,
            skill_context_enabled=True,
        )

        self.assertEqual(2, len(history))
        self.assertEqual(65, client.last_kwargs["limit"])

    async def test_should_remove_duplicate_self_message_echoes(self) -> None:
        """同一条代理回复的双重回显只占一个上下文位置。"""
        manager = MemoryManager(DuplicateEchoEventCenterClient())
        event = UnifiedEvent(
            eventId="current-after-echo",
            platform="qq",
            scene="life",
            eventType="message",
            chatType="private",
            chatId="friend-001",
            selfId="3969785168",
            sender=Sender(id="friend-001", name="小明", role=None),
            text="600 安徽",
            timestamp="2026-07-15T16:33:20+08:00",
            rawPayload={},
        )
        profile_match = ConversationProfileMatchResult(
            matched=True,
            active=True,
            profile=ConversationProfile(
                id="profile-deduplicate",
                name="回显去重",
                privateHistoryEnabled=False,
            ),
        )

        history = await manager.build_history_context(event, profile_match)

        self.assertEqual(2, len(history))
        self.assertEqual(1, sum(item["text"] == "先说下你的选科" for item in history))

    def test_should_not_treat_peer_history_as_self_when_sender_ids_are_complete(self) -> None:
        """训练授权来源会覆盖整段历史，但不能覆盖明确的双方账号 ID。"""
        self.assertFalse(
            MemoryManager._is_self_message("friend-001", "3969785168", "HISTORY_CONSENTED")
        )
        self.assertTrue(
            MemoryManager._is_self_message("3969785168", "3969785168", "HISTORY_CONSENTED")
        )

    async def test_should_drop_previous_session_for_unrelated_new_message(self) -> None:
        """普通新消息与上一轮相隔过久时，旧代理草稿不能再作为当前状态注入。"""
        manager = MemoryManager(StaleHistoryEventCenterClient())
        event = UnifiedEvent(
            eventId="current-002",
            platform="qq",
            scene="life",
            eventType="message",
            chatType="private",
            chatId="friend-001",
            selfId="3969785168",
            sender=Sender(id="friend-001", name="小明", role=None),
            text="你好",
            timestamp="2026-07-13T19:09:21+08:00",
            rawPayload={},
        )
        profile_match = ConversationProfileMatchResult(
            matched=True,
            active=True,
            profile=ConversationProfile(
                id="profile-002",
                name="跨会话隔离",
                privateHistoryEnabled=True,
            ),
        )

        history = await manager.build_history_context(event, profile_match)

        self.assertEqual([], history)

    async def test_should_keep_previous_session_when_user_explicitly_asks_about_it(self) -> None:
        """用户明确追问过去内容时仍保留历史，并标出代理消息不是用户事实。"""
        manager = MemoryManager(StaleHistoryEventCenterClient())
        event = UnifiedEvent(
            eventId="current-003",
            platform="qq",
            scene="life",
            eventType="message",
            chatType="private",
            chatId="friend-001",
            selfId="3969785168",
            sender=Sender(id="friend-001", name="小明", role=None),
            text="你刚才说在干嘛",
            timestamp="2026-07-13T19:09:21+08:00",
            rawPayload={},
        )
        profile_match = ConversationProfileMatchResult(
            matched=True,
            active=True,
            profile=ConversationProfile(
                id="profile-003",
                name="历史追问",
                privateHistoryEnabled=True,
            ),
        )

        history = await manager.build_history_context(event, profile_match)

        self.assertEqual(2, len(history))
        self.assertEqual("agent_output", history[-1]["factAuthority"])

    async def test_should_normalize_identity_deduplication_and_order(self) -> None:
        """actorType 应覆盖错误 senderId，精确 ID 应去重，用户主动同文消息仍必须保留。"""
        manager = MemoryManager(UnifiedIdentityEventCenterClient())
        event = UnifiedEvent(
            eventId="current-unified-identity",
            platform="qq",
            scene="life",
            eventType="message",
            chatType="private",
            chatId="friend-001",
            selfId="3969785168",
            sender=Sender(id="friend-001", name="小明", role=None),
            text="那就这样",
            timestamp="2026-07-13T08:05:00Z",
            rawPayload={},
        )
        profile_match = ConversationProfileMatchResult(
            matched=True,
            active=True,
            profile=ConversationProfile(
                id="profile-unified-identity",
                name="统一身份",
                privateHistoryEnabled=True,
            ),
        )

        history = await manager.build_history_context(event, profile_match)

        self.assertEqual(["下午见", "三点可以", "好的", "好的"], [item["text"] for item in history])
        self.assertEqual(["peer", "self", "self", "self"], [item["role"] for item in history])
        self.assertEqual(
            ["peer_statement", "human_self", "agent_output", "human_self"],
            [item["factAuthority"] for item in history],
        )


class HistoryContextCompressorTest(unittest.TestCase):
    """验证上下文压缩只处理较早消息，并保留身份和来源审计信息。"""

    @staticmethod
    def _build_messages(count: int) -> list[dict]:
        """构造角色交替且事件 ID 稳定的历史时间线。"""
        messages: list[dict] = []
        for index in range(count):
            is_owner = index % 3 == 1
            is_agent = index % 3 == 2
            actor_type = "OWNER" if is_owner else "AGENT" if is_agent else "CONTACT"
            messages.append(
                {
                    "eventId": f"event-{index:02d}",
                    "role": "self" if is_owner or is_agent else "peer",
                    "actorType": actor_type,
                    "messageOrigin": "AGENT_AUTO" if is_agent else "USER_MANUAL" if is_owner else "EXTERNAL",
                    "factAuthority": "agent_output" if is_agent else "human_self" if is_owner else "peer_statement",
                    "text": f"第{index}条消息内容",
                    "timestamp": f"2026-07-19T10:{index:02d}:00+08:00",
                }
            )
        return messages

    def test_should_keep_context_unchanged_when_within_budget(self) -> None:
        """未达到压力阈值时不能凭空创建摘要或改写原始消息。"""
        compressor = HistoryContextCompressor()
        messages = self._build_messages(3)

        result = compressor.compress(messages, max_messages=6, max_chars=500)

        self.assertIs(messages, result)
        self.assertFalse(any(item.get("derivedSummary") for item in result))

    def test_should_summarize_only_older_messages_and_keep_recent_exact(self) -> None:
        """超限后第一项为派生摘要，最近四条仍保持原文和顺序。"""
        compressor = HistoryContextCompressor()
        messages = self._build_messages(10)

        result = compressor.compress(messages, max_messages=5, max_chars=300)

        self.assertEqual(5, len(result))
        summary = result[0]
        self.assertTrue(summary["derivedSummary"])
        self.assertEqual("derived_summary", summary["factAuthority"])
        self.assertEqual("SYSTEM", summary["actorType"])
        self.assertEqual(6, summary["sourceCount"])
        self.assertEqual([f"event-{index:02d}" for index in range(6)], summary["sourceEventIds"])
        self.assertEqual(
            ["第6条消息内容", "第7条消息内容", "第8条消息内容", "第9条消息内容"],
            [item["text"] for item in result[1:]],
        )

    def test_should_preserve_speaker_labels_in_derived_summary(self) -> None:
        """摘要必须区分对方、账号主人和代理代发，不能把三类发言混成用户事实。"""
        compressor = HistoryContextCompressor()
        messages = self._build_messages(7)

        result = compressor.compress(messages, max_messages=4, max_chars=260)

        summary_text = result[0]["text"]
        self.assertIn("对方：第0条消息内容", summary_text)
        self.assertIn("我：第1条消息内容", summary_text)
        self.assertIn("代理曾发送：第2条消息内容", summary_text)


if __name__ == "__main__":
    unittest.main()
