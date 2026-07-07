from __future__ import annotations

import unittest

from app.router.service import RouterService
from app.schemas.events import Sender, UnifiedEvent


class RouterServiceTest(unittest.TestCase):
    def test_should_route_normal_group_message_to_message_dispatch(self) -> None:
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


if __name__ == "__main__":
    unittest.main()
