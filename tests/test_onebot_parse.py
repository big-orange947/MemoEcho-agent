# -*- coding: utf-8 -*-
"""OneBot 解析器测试(app/bridge/onebot.py)。

覆盖:
- 消息段解析(文本/@/图片/文件/未知类型/字符串格式);
- 事件分流(私聊/群聊/自发回显/通知/请求/元事件);
- 群聊 @ 判定;
- 幂等 ID(同 message_id → 同 event_id)。
"""
from __future__ import annotations

from app.bridge import onebot
from app.content import PartType


# ---------------------------------------------------------------------------
# 消息段解析
# ---------------------------------------------------------------------------
class TestParseContent:
    def test_array_format(self):
        """标准 OneBot 数组格式(本项目 NapCat 的实际配置)。"""
        raw = [
            {"type": "text", "data": {"text": "你好"}},
            {"type": "image", "data": {"file": "a.jpg", "url": "http://x/a.jpg"}},
        ]
        parts = onebot.parse_content(raw)
        assert [p.type for p in parts] == [PartType.TEXT, PartType.IMAGE]
        assert parts[0].text == "你好"
        assert parts[1].data["url"] == "http://x/a.jpg"

    def test_string_format(self):
        """字符串格式(旧格式或 messagePostFormat=string)。"""
        parts = onebot.parse_content("纯文本消息")
        assert len(parts) == 1
        assert parts[0].type == PartType.TEXT
        assert parts[0].text == "纯文本消息"

    def test_empty_and_none(self):
        assert onebot.parse_content(None) == []
        assert onebot.parse_content("") == []
        assert onebot.parse_content([]) == []

    def test_full_segment_coverage(self):
        """覆盖文档中列出的所有接收侧段类型,确认不抛异常且类型正确。"""
        raw = [
            {"type": "text", "data": {"text": "t"}},
            {"type": "at", "data": {"qq": "123"}},
            {"type": "reply", "data": {"id": "456"}},
            {"type": "face", "data": {"id": "1"}},
            {"type": "mface", "data": {"emoji_id": "2"}},
            {"type": "dice", "data": {"result": "3"}},
            {"type": "rps", "data": {"result": "1"}},
            {"type": "poke", "data": {"type": "1", "id": "2"}},
            {"type": "image", "data": {"file": "a.jpg"}},
            {"type": "record", "data": {"file": "v.amr"}},
            {"type": "video", "data": {"file": "v.mp4"}},
            {"type": "file", "data": {"file": "doc.pdf"}},
            {"type": "json", "data": {"data": "{}"}},
            {"type": "forward", "data": {"id": "999"}},
            {"type": "music", "data": {"type": "qq", "id": "1"}},
        ]
        parts = onebot.parse_content(raw)
        assert len(parts) == len(raw)
        assert [p.type for p in parts] == [seg["type"] for seg in raw]

    def test_unknown_type_preserved(self):
        """未知段类型必须保留原始类型名与数据(不丢信息)。"""
        raw = [{"type": "brand_new_type", "data": {"foo": "bar"}}]
        parts = onebot.parse_content(raw)
        assert parts[0].type == PartType.UNKNOWN
        assert parts[0].data["raw_type"] == "brand_new_type"
        assert parts[0].data["foo"] == "bar"

    def test_non_dict_segment(self):
        """数组里混入非字典元素时也不崩。"""
        parts = onebot.parse_content(["oops", {"type": "text", "data": {"text": "ok"}}])
        assert parts[0].type == PartType.UNKNOWN
        assert parts[1].text == "ok"


# ---------------------------------------------------------------------------
# 事件构造与分流
# ---------------------------------------------------------------------------
class TestBuildEvent:
    def test_private_message(self):
        body = {
            "post_type": "message",
            "message_type": "private",
            "user_id": 10001,
            "message_id": 555,
            "message": [{"type": "text", "data": {"text": "在吗"}}],
        }
        event = onebot.build_event(body, bot_qq="3969785168")
        assert event is not None
        assert event.kind == onebot.KIND_MESSAGE
        assert event.should_respond is True
        assert event.chat_type == "private"
        assert event.external_id == "10001"
        assert event.text == "在吗"
        assert event.is_self is False

    def test_private_message_with_at(self):
        """私聊里的 @ 不影响是否回应(私聊一律回应)。"""
        body = {
            "post_type": "message",
            "message_type": "private",
            "user_id": 10001,
            "message_id": 556,
            "message": [
                {"type": "at", "data": {"qq": "3969785168"}},
                {"type": "text", "data": {"text": " hi"}},
            ],
        }
        event = onebot.build_event(body, bot_qq="3969785168")
        assert event.should_respond is True
        assert event.text == "@3969785168 hi"

    def test_group_message_without_at_not_responded(self):
        """群聊未 @ 机器人 → 不回应(默认策略)。"""
        body = {
            "post_type": "message",
            "message_type": "group",
            "group_id": 88888,
            "user_id": 10001,
            "message_id": 557,
            "message": [{"type": "text", "data": {"text": "闲聊"}}],
        }
        event = onebot.build_event(body, bot_qq="3969785168")
        assert event.chat_type == "group"
        assert event.external_id == "88888"
        assert event.should_respond is False

    def test_group_message_with_at_responded(self):
        """群聊 @ 了机器人 → 回应。"""
        body = {
            "post_type": "message",
            "message_type": "group",
            "group_id": 88888,
            "user_id": 10001,
            "message_id": 558,
            "message": [
                {"type": "at", "data": {"qq": "3969785168"}},
                {"type": "text", "data": {"text": " 帮我看看"}},
            ],
        }
        event = onebot.build_event(body, bot_qq="3969785168")
        assert event.should_respond is True

    def test_group_at_all_does_not_trigger(self):
        """@全体成员不算 @ 机器人(否则群里每条 @all 都会唤醒)。"""
        body = {
            "post_type": "message",
            "message_type": "group",
            "group_id": 88888,
            "user_id": 10001,
            "message_id": 559,
            "message": [
                {"type": "at", "data": {"qq": "all"}},
                {"type": "text", "data": {"text": " 通知"}},
            ],
        }
        event = onebot.build_event(body, bot_qq="3969785168")
        assert event.should_respond is False

    def test_group_require_at_disabled(self):
        """配置关闭"必须 @"后,群聊普通消息也回应。"""
        body = {
            "post_type": "message",
            "message_type": "group",
            "group_id": 88888,
            "user_id": 10001,
            "message_id": 560,
            "message": [{"type": "text", "data": {"text": "随便说说"}}],
        }
        event = onebot.build_event(body, bot_qq="3969785168", group_require_at=False)
        assert event.should_respond is True

    def test_message_sent_never_responds(self):
        """机器人自发消息回显: 绝不回应(防自循环)。"""
        body = {
            "post_type": "message_sent",
            "message_type": "private",
            "user_id": 10001,
            "self_id": 3969785168,
            "message_id": 561,
            "message": [{"type": "text", "data": {"text": "我自己发的"}}],
        }
        event = onebot.build_event(body, bot_qq="3969785168")
        assert event.kind == onebot.KIND_MESSAGE_SENT
        assert event.should_respond is False
        assert event.is_self is True

    def test_notice_event(self):
        body = {
            "post_type": "notice",
            "notice_type": "friend_recall",
            "user_id": 10001,
            "message_id": 562,
        }
        event = onebot.build_event(body, bot_qq="3969785168")
        assert event.kind == onebot.KIND_NOTICE
        assert event.should_respond is False
        assert "撤回" in event.text

    def test_group_upload_notice(self):
        body = {
            "post_type": "notice",
            "notice_type": "group_upload",
            "group_id": 88888,
            "user_id": 10001,
            "file": {"name": "报告.pdf", "size": 1024},
        }
        event = onebot.build_event(body, bot_qq="")
        assert "报告.pdf" in event.text

    def test_poke_notice(self):
        body = {
            "post_type": "notice",
            "notice_type": "notify",
            "sub_type": "poke",
            "user_id": 10001,
            "target_id": 3969785168,
        }
        event = onebot.build_event(body, bot_qq="")
        assert "戳一戳" in event.text

    def test_request_event(self):
        body = {
            "post_type": "request",
            "request_type": "friend",
            "user_id": 10001,
            "comment": "我是小明",
            "flag": "abc",
        }
        event = onebot.build_event(body, bot_qq="")
        assert event.kind == onebot.KIND_REQUEST
        assert event.should_respond is False
        assert "好友申请" in event.text
        assert "我是小明" in event.text

    def test_meta_event_returns_none(self):
        """心跳/生命周期不入业务链路。"""
        assert onebot.build_event({"post_type": "meta_event", "meta_event_type": "heartbeat"}, bot_qq="") is None

    def test_unknown_post_type(self):
        event = onebot.build_event({"post_type": "something_new"}, bot_qq="")
        assert event.kind == onebot.KIND_UNKNOWN
        assert event.should_respond is False


# ---------------------------------------------------------------------------
# 幂等 ID
# ---------------------------------------------------------------------------
class TestEventId:
    def test_same_message_id_gives_same_event_id(self):
        """平台重推同一消息 → 相同 event_id → 上层可据此去重。"""
        body = {
            "post_type": "message",
            "message_type": "private",
            "user_id": 10001,
            "message_id": 777,
            "message": [{"type": "text", "data": {"text": "重复推送"}}],
        }
        e1 = onebot.build_event(body, bot_qq="")
        e2 = onebot.build_event(body, bot_qq="")
        assert e1.event_id == e2.event_id

    def test_different_message_id_gives_different_event_id(self):
        base = {
            "post_type": "message",
            "message_type": "private",
            "user_id": 10001,
            "message": [{"type": "text", "data": {"text": "x"}}],
        }
        e1 = onebot.build_event({**base, "message_id": 1}, bot_qq="")
        e2 = onebot.build_event({**base, "message_id": 2}, bot_qq="")
        assert e1.event_id != e2.event_id

    def test_notice_without_message_id_still_unique(self):
        """通知类事件没有 message_id,但时间戳不同即可区分。"""
        e1 = onebot.build_event(
            {"post_type": "notice", "notice_type": "friend_recall", "user_id": 1, "time": 100},
            bot_qq="",
        )
        e2 = onebot.build_event(
            {"post_type": "notice", "notice_type": "friend_recall", "user_id": 1, "time": 200},
            bot_qq="",
        )
        assert e1.event_id != e2.event_id


# ---------------------------------------------------------------------------
# 空消息兜底
# ---------------------------------------------------------------------------
class TestFallback:
    def test_raw_message_fallback(self):
        """message 为空但 raw_message 有值(某些纯表情场景)。"""
        body = {
            "post_type": "message",
            "message_type": "private",
            "user_id": 10001,
            "message_id": 900,
            "message": [],
            "raw_message": "[表情]",
        }
        event = onebot.build_event(body, bot_qq="")
        assert event.text == "[表情]"
