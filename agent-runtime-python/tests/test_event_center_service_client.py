from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from app.clients.event_center_service import EventCenterServiceClient
from app.schemas.events import Sender, UnifiedEvent


class EventCenterServiceClientTest(unittest.TestCase):
    """验证 runtime 调用 event-center 时不会遗漏服务认证头。"""

    def test_shouldAttachRuntimeTokenAndUserIdWhenConfigured(self) -> None:
        """配置服务令牌后，模型解析请求应同时携带令牌和当前运行时用户标识。"""
        with patch.dict(os.environ, {"EVENT_CENTER_RUNTIME_TOKEN": "runtime-test-token"}, clear=False):
            headers = EventCenterServiceClient._runtime_headers("freeze")

        self.assertEqual("runtime-test-token", headers["X-Memo-Echo-Runtime-Token"])
        self.assertEqual("freeze", headers["X-Memo-Echo-User-Id"])

    def test_shouldKeepMigrationCompatibilityWithoutRuntimeToken(self) -> None:
        """未配置令牌时不伪造认证头，交由后端的迁移开关决定是否接受旧调用。"""
        with patch.dict(os.environ, {}, clear=True):
            headers = EventCenterServiceClient._runtime_headers("freeze")

        self.assertEqual({}, headers)

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
