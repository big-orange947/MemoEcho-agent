from __future__ import annotations

import unittest

from app.router.service import RouterService
from app.schemas.events import Sender, UnifiedEvent
from app.schemas.profiles import ConversationProfile, ConversationProfileMatchResult


class RouterServiceTest(unittest.TestCase):
    def test_should_route_normal_group_message_to_message_dispatch(self) -> None:
        # 这个测试函数的作用是验证普通群消息默认先进入双通道分发链路。
        router = RouterService()
        event = UnifiedEvent(
            eventId="qq:message:group:20001",
            platform="qq",
            scene="life",
            eventType="message",
            chatType="group",
            chatId="138178088",
            sender=Sender(id="10001", name="alice", role=None),
            text="晚上一起吃饭吗",
            attachments=[],
            mentions=[],
            timestamp="2026-07-06T17:10:00+08:00",
            rawPayload={"self_id": 3969785168},
        )

        route = router.route(event)

        self.assertEqual(route, "message_dispatch")

    def test_should_route_at_self_message_to_existing_domain_flow(self) -> None:
        # 这个测试函数的作用是验证被 @ 的群消息会继续按具体意图进入业务链路。
        router = RouterService()
        event = UnifiedEvent(
            eventId="qq:message:group:20002",
            platform="qq",
            scene="life",
            eventType="message",
            chatType="group",
            chatId="138178088",
            sender=Sender(id="10001", name="alice", role=None),
            text="[CQ:at,qq=3969785168] schedule for today",
            attachments=[],
            mentions=["3969785168"],
            timestamp="2026-07-06T17:11:00+08:00",
            rawPayload={"self_id": 3969785168},
        )

        route = router.route(event)

        self.assertEqual(route, "schedule_extract")

    def test_should_route_at_self_message_when_mentions_missing_but_raw_payload_has_at_segment(self) -> None:
        # 这个测试函数的作用是验证没有 mentions 时也能从 NapCat 原始分段识别 @ 自己。
        router = RouterService()
        event = UnifiedEvent(
            eventId="qq:message:group:20003",
            platform="qq",
            scene="life",
            eventType="message",
            chatType="group",
            chatId="138178088",
            sender=Sender(id="10001", name="alice", role=None),
            text="[CQ:at,qq=3969785168] schedule for today",
            attachments=[],
            mentions=[],
            timestamp="2026-07-07T15:45:00+08:00",
            rawPayload={
                "self_id": 3969785168,
                "message": [
                    {"type": "at", "data": {"qq": "3969785168"}},
                    {"type": "text", "data": {"text": " schedule for today"}},
                ],
            },
        )

        route = router.route(event)

        self.assertEqual(route, "schedule_extract")

    def test_should_route_chinese_task_message_to_task_plan(self) -> None:
        # 这个测试函数的作用是验证中文任务创建消息会进入任务规划链路。
        router = RouterService()
        event = UnifiedEvent(
            eventId="qq:message:group:20004",
            platform="qq",
            scene="work",
            eventType="message",
            chatType="group",
            chatId="1098307542",
            selfId="3969785168",
            sender=Sender(id="10001", name="alice", role=None),
            text="[CQ:at,qq=3969785168] 请今天整理项目汇总并提交",
            attachments=[],
            mentions=["3969785168"],
            timestamp="2026-07-07T16:30:00+08:00",
            rawPayload={"self_id": 3969785168},
        )

        route = router.route(event)

        self.assertEqual(route, "task_plan")

    def test_should_route_task_query_message_to_task_plan(self) -> None:
        # 这个测试函数的作用是验证“我今天该做什么”这类待办查询也会进入任务规划链路。
        router = RouterService()
        event = UnifiedEvent(
            eventId="qq:message:private:20005",
            platform="qq",
            scene="work",
            eventType="message",
            chatType="private",
            chatId="2597164807",
            sender=Sender(id="2597164807", name="freeze", role=None),
            text="我今天该做什么？",
            attachments=[],
            mentions=[],
            timestamp="2026-07-07T16:35:00+08:00",
            rawPayload={},
        )

        route = router.route(event)

        self.assertEqual(route, "task_plan")

    def test_should_use_active_profile_preferred_route_first(self) -> None:
        # 这个测试函数的作用是验证设定集激活后可以直接覆盖默认路由结果。
        router = RouterService()
        event = UnifiedEvent(
            eventId="qq:message:private:20006",
            platform="qq",
            scene="life",
            eventType="message",
            chatType="private",
            chatId="2597164807",
            sender=Sender(id="10001", name="alice", role=None),
            text="只是普通聊天",
            attachments=[],
            mentions=[],
            timestamp="2026-07-07T16:40:00+08:00",
            rawPayload={},
        )
        profile_match = ConversationProfileMatchResult(
            matched=True,
            active=True,
            reason="命中会话范围且满足触发条件",
            profile=ConversationProfile(
                id="profile-001",
                name="私聊人格",
                preferredRoute="social_reply",
            ),
        )

        route = router.route(event, profile_match)

        self.assertEqual(route, "social_reply")

    def test_should_use_allowed_route_from_desktop_command(self) -> None:
        # 这个测试函数的作用是验证桌面工作台可以显式选择已注册的 Agent 工作流。
        event = UnifiedEvent(
            eventId="desktop:command:1",
            platform="desktop",
            scene="workspace",
            eventType="desktop_command",
            chatType="private",
            chatId="workspace:user-001",
            sender=Sender(id="user-001", name="freeze", role="owner"),
            text="总结最近重要消息",
            attachments=[],
            mentions=[],
            timestamp="2026-07-11T10:00:00+08:00",
            rawPayload={"userId": "user-001", "requestedRoute": "chat_summary"},
        )

        self.assertEqual(RouterService().route(event), "chat_summary")

    def test_should_ignore_unknown_route_from_desktop_command(self) -> None:
        # 这个测试函数的作用是验证未知桌面路由不会绕过 Router 的正常意图判断。
        event = UnifiedEvent(
            eventId="desktop:command:2",
            platform="desktop",
            scene="workspace",
            eventType="desktop_command",
            chatType="private",
            chatId="workspace:user-001",
            sender=Sender(id="user-001", name="freeze", role="owner"),
            text="我今天该做什么？",
            attachments=[],
            mentions=[],
            timestamp="2026-07-11T10:00:00+08:00",
            rawPayload={"userId": "user-001", "requestedRoute": "internal_admin"},
        )

        self.assertEqual(RouterService().route(event), "task_plan")


if __name__ == "__main__":
    unittest.main()
