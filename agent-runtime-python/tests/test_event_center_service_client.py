from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.clients.event_center_service import EventCenterServiceClient
from app.schemas.events import Sender, UnifiedEvent


class EventCenterServiceClientTest(unittest.IsolatedAsyncioTestCase):
    """验证 runtime 调用 event-center 时不会遗漏服务认证头。"""

    def test_shouldAttachRuntimeTokenAndUserIdWhenConfigured(self) -> None:
        """配置服务令牌后，模型解析请求应同时携带令牌和当前运行时用户标识。"""
        with patch.dict(os.environ, {"EVENT_CENTER_RUNTIME_TOKEN": "runtime-test-token"}, clear=False):
            headers = EventCenterServiceClient._runtime_headers("freeze")

        self.assertEqual("runtime-test-token", headers["X-Memo-Echo-Runtime-Token"])
        self.assertEqual("freeze", headers["X-Memo-Echo-User-Id"])

    def test_shouldUseLocalRuntimeTokenByDefault(self) -> None:
        """本地未设置环境变量时仍应携带共享开发令牌，避免设定匹配静默返回 401。"""
        with patch.dict(os.environ, {}, clear=True):
            headers = EventCenterServiceClient._runtime_headers("freeze")

        self.assertEqual("memo-echo-local-runtime-token", headers["X-Memo-Echo-Runtime-Token"])
        self.assertEqual("freeze", headers["X-Memo-Echo-User-Id"])

    def test_shouldResolveDesktopUserFromEventPayload(self) -> None:
        """桌面事件应优先使用 event-center 写入的用户 ID，保证设定集与模型配置都按用户隔离。"""
        event = UnifiedEvent(
            eventId="desktop:command:1",
            platform="desktop",
            scene="workspace",
            eventType="desktop_command",
            chatType="private",
            chatId="workspace:user-001",
            sender=Sender(id="user-001", name="freeze", role="owner"),
            text="总结消息",
            attachments=[],
            mentions=[],
            timestamp="2026-07-11T10:00:00+08:00",
            rawPayload={"userId": "user-001"},
        )

        self.assertEqual("user-001", EventCenterServiceClient._resolve_event_user_id(event))

    async def test_shouldResolveSecureAssetWithRuntimeHeaders(self) -> None:
        """安全资产解析请求必须携带 Runtime Token 和当前用户 ID。"""
        response = MagicMock()
        response.json.return_value = {"id": "asset-1", "content": "secret"}
        post = AsyncMock(return_value=response)
        http_client = MagicMock()
        http_client.post = post
        client_context = MagicMock()
        client_context.__aenter__ = AsyncMock(return_value=http_client)
        client_context.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=client_context):
            with patch.dict(os.environ, {"EVENT_CENTER_RUNTIME_TOKEN": "runtime-test-token"}, clear=False):
                payload = await EventCenterServiceClient("http://event-center").resolve_secure_asset(
                    "asset-1", "freeze"
                )

        self.assertEqual("asset-1", payload["id"])
        _, kwargs = post.call_args
        self.assertEqual("runtime-test-token", kwargs["headers"]["X-Memo-Echo-Runtime-Token"])
        self.assertEqual("freeze", kwargs["headers"]["X-Memo-Echo-User-Id"])

    async def test_shouldQueryVerifiedMemoriesWithCurrentConversationScope(self) -> None:
        """读取长期记忆时必须传递平台、场景和会话作用域，并携带当前用户认证头。"""
        response = MagicMock()
        response.json.return_value = [
            {
                "id": "memory-client-001",
                "subject": "对方",
                "predicate": "常用称呼",
                "value": "小明",
                "scopeType": "CONVERSATION",
                "platform": "qq",
                "scene": "life",
                "chatType": "private",
                "chatId": "friend-001",
                "sourceEventIds": ["owner-event-001"],
                "sourceActorType": "OWNER",
                "factAuthority": "human_self",
                "confidence": 0.9,
                "status": "VERIFIED",
            }
        ]
        get = AsyncMock(return_value=response)
        http_client = MagicMock()
        http_client.get = get
        client_context = MagicMock()
        client_context.__aenter__ = AsyncMock(return_value=http_client)
        client_context.__aexit__ = AsyncMock(return_value=None)
        event = UnifiedEvent(
            eventId="qq:message:private:memory-001",
            platform="qq",
            scene="life",
            eventType="message",
            chatType="private",
            chatId="friend-001",
            sender=Sender(id="friend-001", name="小明", role=None),
            text="下午还去吗",
            timestamp="2026-07-17T10:00:00+08:00",
            rawPayload={"userId": "freeze"},
        )

        with patch("httpx.AsyncClient", return_value=client_context):
            memories = await EventCenterServiceClient("http://event-center").list_verified_memories(event)

        self.assertEqual("memory-client-001", memories[0].id)
        _, kwargs = get.call_args
        self.assertEqual(
            {"platform": "qq", "scene": "life", "chatType": "private", "chatId": "friend-001"},
            kwargs["params"],
        )
        self.assertEqual("freeze", kwargs["headers"]["X-Memo-Echo-User-Id"])

    async def test_shouldRestoreActiveDelegatedTaskForCurrentConversation(self) -> None:
        """活动委托查询必须携带完整会话键和当前用户，避免恢复到其他会话的任务。"""
        response = MagicMock(status_code=200)
        response.json.return_value = {"id": "task-1", "status": "ACTIVE"}
        get = AsyncMock(return_value=response)
        http_client = MagicMock(get=get)
        client_context = MagicMock()
        client_context.__aenter__ = AsyncMock(return_value=http_client)
        client_context.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=client_context):
            task = await EventCenterServiceClient("http://event-center").get_active_delegated_task(
                self._delegated_event()
            )

        self.assertEqual("task-1", task["id"])
        _, kwargs = get.call_args
        self.assertEqual(
            {"platform": "qq", "chatType": "private", "chatId": "friend-001"},
            kwargs["params"],
        )
        self.assertEqual("freeze", kwargs["headers"]["X-Memo-Echo-User-Id"])

    async def test_shouldTreatMissingActiveDelegatedTaskAsNormalConversation(self) -> None:
        """服务端返回 404 表示当前会话没有活动委托，不应把它当成运行时异常。"""
        response = MagicMock(status_code=404)
        get = AsyncMock(return_value=response)
        http_client = MagicMock(get=get)
        client_context = MagicMock()
        client_context.__aenter__ = AsyncMock(return_value=http_client)
        client_context.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=client_context):
            task = await EventCenterServiceClient("http://event-center").get_active_delegated_task(
                self._delegated_event()
            )

        self.assertIsNone(task)
        response.raise_for_status.assert_not_called()

    async def test_shouldPersistDelegatedTaskRuntimeState(self) -> None:
        """每轮 LangGraph 结果必须完整回传，才能在进程或客户端重启后继续原任务。"""
        response = MagicMock(status_code=200)
        response.json.return_value = {"id": "task-1", "status": "ACTIVE"}
        post = AsyncMock(return_value=response)
        http_client = MagicMock(post=post)
        client_context = MagicMock()
        client_context.__aenter__ = AsyncMock(return_value=http_client)
        client_context.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=client_context):
            result = await EventCenterServiceClient("http://event-center").update_delegated_task_runtime(
                self._delegated_event(),
                "task-1",
                status="ACTIVE",
                progress_summary="已确认明天下午有空",
                state_json='{"knownFacts":["明天下午有空"]}',
                last_event_id="event-2",
                completion_report="",
            )

        self.assertEqual("ACTIVE", result["status"])
        _, kwargs = post.call_args
        self.assertEqual(
            {
                "status": "ACTIVE",
                "progressSummary": "已确认明天下午有空",
                "stateJson": '{"knownFacts":["明天下午有空"]}',
                "lastEventId": "event-2",
                "completionReport": "",
            },
            kwargs["json"],
        )
        self.assertEqual("freeze", kwargs["headers"]["X-Memo-Echo-User-Id"])

    @staticmethod
    def _delegated_event() -> UnifiedEvent:
        """构造带用户归属和时间戳的私聊事件，供委托恢复与回传测试复用。"""
        return UnifiedEvent(
            eventId="qq:message:private:event-2",
            platform="qq",
            scene="life",
            eventType="message",
            chatType="private",
            chatId="friend-001",
            sender=Sender(id="friend-001", name="小明", role=None),
            text="明天下午有空",
            timestamp="2026-07-21T10:00:00+08:00",
            rawPayload={"userId": "freeze"},
        )
