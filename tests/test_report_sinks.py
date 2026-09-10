# -*- coding: utf-8 -*-
"""上报出口(sink)测试: 队列里的上报要真正送得出去。

背景: report_queue 只做到"发现 + 排队",队列本身到不了人 ——
上游还没接上时,"重要消息自主汇报"端到端是断的。本文件守住这条出口。

要守住的几件事:
1. 未启用出口时**什么都不做**(默认安全,不打扰);
2. 启用后能送出,且**不重复打扰**(投递完即 ack,上游不会再来一遍);
3. 急事单独成条、普通消息合并摘要(防刷屏);
4. 投递失败**不丢消息**(不 ack,等租约到期重试);
5. 限流只暂缓不丢弃,且 urgent 不受限;
6. 请示(question)单独成条 —— 混进摘要里容易被忽略。

不联网、不发真实消息。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app import reports as reports_service
from app import sinks as sinks_service


@pytest.fixture()
def env(temp_data_dir, monkeypatch):
    """建表;默认不启用 qq 出口(各用例按需开)。"""
    monkeypatch.setenv("MEMO_ECHO_BOT_QQ", "3969785168")
    monkeypatch.setenv("MEMO_ECHO_ALERT_SINKS", "db")
    monkeypatch.setenv("MEMO_ECHO_ALERT_FORWARD_TARGET", "")

    from app import config as config_module

    monkeypatch.setattr(config_module, "_settings", None, raising=False)

    from app.db import init_db

    init_db()
    return None


def _enable_qq(monkeypatch, target: str = "private:10000", cap: int = 10) -> None:
    monkeypatch.setenv("MEMO_ECHO_ALERT_SINKS", "db,qq")
    monkeypatch.setenv("MEMO_ECHO_ALERT_FORWARD_TARGET", target)
    monkeypatch.setenv("MEMO_ECHO_ALERT_MAX_PER_HOUR", str(cap))

    from app import config as config_module

    config_module._settings = None


def _enqueue(
    *,
    lane: str = reports_service.LANE_NORMAL,
    conversation_id: str = "conv-a",
    message_id: str = "m1",
    text: str = "你今晚还来不来？",
    sender_name: str = "km",
    summary: str = "",
    options: str = "",
    title: str = "km",
) -> dict:
    """造一条待投递记录(跳过候选阶段,直接进 pending)。"""
    from app.services import conversations as conversations_service

    conv_id = conversations_service.ensure_conversation("qq", "private", conversation_id)
    conversations_service.update_profile(conv_id, title=title)
    return reports_service.enqueue(
        lane=lane,
        conversation_id=conv_id,
        message_ids=[message_id],
        payload={
            "text": text,
            "sender_name": sender_name,
            "summary": summary or text,
            "options": options,
            "reasons": ["keyword:今晚"],
        },
        dedup_key=message_id,
        status=reports_service.STATUS_PENDING,
    )


# ---------------------------------------------------------------------------
# 配置解析
# ---------------------------------------------------------------------------
class TestParsing:
    def test_default_sinks_is_db_only(self):
        assert sinks_service.parse_sinks("") == ["db"]

    def test_multiple_sinks(self):
        assert sinks_service.parse_sinks("db, qq") == ["db", "qq"]
        assert sinks_service.parse_sinks("db;qq") == ["db", "qq"]

    def test_target_forms(self):
        assert sinks_service.parse_forward_target("123456") == ("private", "123456")
        assert sinks_service.parse_forward_target("private:123456") == ("private", "123456")
        assert sinks_service.parse_forward_target("group:789012") == ("group", "789012")
        assert sinks_service.parse_forward_target("qq:private:123456") == ("private", "123456")

    def test_bad_target_is_none(self):
        assert sinks_service.parse_forward_target("") is None
        assert sinks_service.parse_forward_target("wechat:123") is None


# ---------------------------------------------------------------------------
# 文案
# ---------------------------------------------------------------------------
class TestFormatting:
    def test_single_report_has_label_and_original(self):
        record = {
            "lane": "urgent",
            "conversation_id": "c1",
            "payload": {"text": "我七点就走", "summary": "对方七点要走", "sender_name": "km", "reasons": ["keyword:今晚"]},
        }
        text = sinks_service.format_report(record, {"title": "km", "chat_type": "private", "external_id": "1"})
        assert "急" in text
        assert "km" in text
        assert "对方七点要走" in text
        assert "我七点就走" in text          # 原话要带上(摘要可能丢细节)
        assert "keyword:今晚" in text

    def test_question_includes_options(self):
        record = {
            "lane": "question",
            "conversation_id": "c1",
            "payload": {"summary": "km 说八点不行,改九点?", "options": "① 同意 ② 改天"},
        }
        text = sinks_service.format_report(record, {"title": "km"})
        assert "需要你决定" in text
        assert "① 同意" in text

    def test_digest_counts_and_lists(self):
        records = [
            {"lane": "normal", "conversation_id": "c1", "payload": {"summary": "甲的事", "sender_name": "甲"}},
            {"lane": "digest", "conversation_id": "c2", "payload": {"summary": "乙的事", "sender_name": "乙"}},
        ]
        text = sinks_service.format_digest(records, {"c1": {"title": "甲"}, "c2": {"title": "乙"}})
        assert "2 条" in text
        assert "甲的事" in text and "乙的事" in text

    def test_degraded_marker(self):
        """复核没跑成(配额/故障)时要标注,让人知道这条没经过模型判断。"""
        record = {"lane": "normal", "conversation_id": "c1", "payload": {"summary": "x", "degraded": "quota_exhausted"}}
        assert "复核未执行" in sinks_service.format_report(record, {})

    def test_private_chat_name_not_duplicated(self):
        """私聊里会话标题就是对方昵称,不该显示成 "km · km"。"""
        record = {"lane": "urgent", "conversation_id": "c1", "payload": {"summary": "x", "sender_name": "km"}}
        text = sinks_service.format_report(record, {"title": "km"})
        assert "km · km" not in text
        assert text.count("km") == 1

    def test_group_keeps_speaker_name(self):
        """群聊里说话人是有用信息(标题是群名),必须保留。"""
        record = {"lane": "urgent", "conversation_id": "c1", "payload": {"summary": "x", "sender_name": "张三"}}
        text = sinks_service.format_report(record, {"title": "班群", "chat_type": "group"})
        assert "班群" in text and "张三" in text

    def test_digest_uses_subject(self):
        records = [
            {"lane": "normal", "conversation_id": "c1", "payload": {"summary": "甲的事", "sender_name": "甲"}},
        ]
        text = sinks_service.format_digest(records, {"c1": {"title": "甲"}})
        assert "甲 · 甲" not in text


# ---------------------------------------------------------------------------
# 投递
# ---------------------------------------------------------------------------
class TestDeliver:
    @pytest.mark.asyncio
    async def test_disabled_sink_does_nothing(self, env):
        """默认(只 db)时不该有任何投递 —— 安全默认,不打扰人。"""
        sent: list[tuple[str, str]] = []

        async def send(conversation_id: str, text: str) -> bool:
            sent.append((conversation_id, text))
            return True

        _enqueue()
        result = await sinks_service.deliver_pending(send=send)

        assert result["enabled"] is False
        assert sent == []
        # 记录仍在队列里等上游
        assert reports_service.stats().get(reports_service.STATUS_PENDING) == 1

    @pytest.mark.asyncio
    async def test_missing_target_reports_reason(self, env, monkeypatch):
        _enable_qq(monkeypatch, target="")
        monkeypatch.setenv("MEMO_ECHO_ALERT_FORWARD_TARGET", "")

        from app import config as config_module

        config_module._settings = None

        async def send(conversation_id: str, text: str) -> bool:  # pragma: no cover
            raise AssertionError("不该投递")

        result = await sinks_service.deliver_pending(send=send)
        assert result["enabled"] is False
        assert "未配置" in result["reason"]

    @pytest.mark.asyncio
    async def test_urgent_delivered_and_acked(self, env, monkeypatch):
        """急事单独成条送出,送完即 ack(上游不会再来一遍 → 不重复打扰)。"""
        _enable_qq(monkeypatch)
        sent: list[tuple[str, str]] = []

        async def send(conversation_id: str, text: str) -> bool:
            sent.append((conversation_id, text))
            return True

        _enqueue(lane=reports_service.LANE_URGENT, text="我七点就走", summary="对方七点要走")
        result = await sinks_service.deliver_pending(send=send)

        assert result["delivered"] == 1
        assert len(sent) == 1
        assert "对方七点要走" in sent[0][1]
        assert reports_service.stats().get(reports_service.STATUS_ACKED) == 1
        assert reports_service.stats().get(reports_service.STATUS_PENDING) is None

    @pytest.mark.asyncio
    async def test_upstream_cannot_see_delivered(self, env, monkeypatch):
        """本地投递过的记录,上游 claim 不到 —— 这是"不重复打扰"的关键。"""
        _enable_qq(monkeypatch)

        async def send(conversation_id: str, text: str) -> bool:
            return True

        _enqueue(lane=reports_service.LANE_URGENT)
        await sinks_service.deliver_pending(send=send)

        assert reports_service.claim(limit=10, claimed_by="main-agent") == []

    @pytest.mark.asyncio
    async def test_normal_messages_merged_into_one_digest(self, env, monkeypatch):
        """普通消息合并成一条摘要 —— 防刷屏。"""
        _enable_qq(monkeypatch)
        sent: list[str] = []

        async def send(conversation_id: str, text: str) -> bool:
            sent.append(text)
            return True

        _enqueue(lane=reports_service.LANE_NORMAL, message_id="m1", text="甲的事", conversation_id="c1")
        _enqueue(lane=reports_service.LANE_NORMAL, message_id="m2", text="乙的事", conversation_id="c2")
        _enqueue(lane=reports_service.LANE_DIGEST, message_id="m3", text="丙的事", conversation_id="c3")

        result = await sinks_service.deliver_pending(send=send)

        assert len(sent) == 1, f"应合并为一条: {sent}"
        assert "3 条" in sent[0]
        assert result["delivered"] == 3

    @pytest.mark.asyncio
    async def test_question_is_standalone(self, env, monkeypatch):
        """请示必须单独成条 —— 混进摘要里容易被忽略。"""
        _enable_qq(monkeypatch)
        sent: list[str] = []

        async def send(conversation_id: str, text: str) -> bool:
            sent.append(text)
            return True

        _enqueue(lane=reports_service.LANE_QUESTION, message_id="q1", summary="改九点行吗?", options="① 同意")
        _enqueue(lane=reports_service.LANE_NORMAL, message_id="n1", text="小事一桩")

        await sinks_service.deliver_pending(send=send)

        assert len(sent) == 2
        assert any("需要你决定" in text for text in sent)
        assert any("小事一桩" in text for text in sent)

    @pytest.mark.asyncio
    async def test_failure_keeps_message_in_queue(self, env, monkeypatch):
        """投递失败不 ack —— 租约到期后回队列重试,消息不会丢。"""
        _enable_qq(monkeypatch)

        async def failing_send(conversation_id: str, text: str) -> bool:
            return False

        _enqueue(lane=reports_service.LANE_URGENT)
        result = await sinks_service.deliver_pending(send=failing_send)

        assert result["failed"] == 1
        assert result["delivered"] == 0
        assert reports_service.stats().get(reports_service.STATUS_CLAIMED) == 1

        # 模拟租约过期 → 自动回队列
        from app.db import get_connection

        conn = get_connection()
        conn.execute("UPDATE report_queue SET lease_expires_at='2000-01-01T00:00:00+00:00'")
        conn.commit()
        reports_service.reap_expired()
        assert reports_service.stats().get(reports_service.STATUS_PENDING) == 1

    @pytest.mark.asyncio
    async def test_exception_does_not_abort_batch(self, env, monkeypatch):
        """一条投递抛异常不能中断整轮 —— 后面的记录照常处理。"""
        _enable_qq(monkeypatch)
        sent: list[str] = []

        async def flaky_send(conversation_id: str, text: str) -> bool:
            if "会炸的" in text:
                raise RuntimeError("机器人掉线了")
            sent.append(text)
            return True

        _enqueue(lane=reports_service.LANE_URGENT, message_id="boom", text="会炸的", sender_name="甲")
        _enqueue(lane=reports_service.LANE_URGENT, message_id="ok", text="正常的", sender_name="乙")

        result = await sinks_service.deliver_pending(send=flaky_send)

        assert result["delivered"] == 1
        assert result["failed"] == 1
        assert "正常的" in sent[0]


# ---------------------------------------------------------------------------
# 限流
# ---------------------------------------------------------------------------
class TestRateLimit:
    @pytest.mark.asyncio
    async def test_cap_defers_but_does_not_drop(self, env, monkeypatch):
        """超过每小时上限的普通消息被暂缓(回到 pending),不是丢弃。"""
        _enable_qq(monkeypatch, cap=2)
        sent: list[str] = []

        async def send(conversation_id: str, text: str) -> bool:
            sent.append(text)
            return True

        for index in range(5):
            _enqueue(lane=reports_service.LANE_NORMAL, message_id=f"m{index}", conversation_id="chatty")

        result = await sinks_service.deliver_pending(send=send)

        assert result["delivered"] == 2, f"上限 2 条却送了 {result['delivered']} 条"
        assert result["deferred"] == 3
        assert reports_service.stats().get(reports_service.STATUS_PENDING) == 3

    @pytest.mark.asyncio
    async def test_urgent_bypasses_cap(self, env, monkeypatch):
        """急事不受限流影响 —— 不能因为"这个会话话太多"把急事压下去。"""
        _enable_qq(monkeypatch, cap=1)
        sent: list[str] = []

        async def send(conversation_id: str, text: str) -> bool:
            sent.append(text)
            return True

        for index in range(3):
            _enqueue(lane=reports_service.LANE_URGENT, message_id=f"u{index}", conversation_id="chatty")

        result = await sinks_service.deliver_pending(send=send)
        assert result["delivered"] == 3

    @pytest.mark.asyncio
    async def test_cap_counts_already_delivered(self, env, monkeypatch):
        """限流要算上"这一小时已经送过的",不是只看本轮。"""
        _enable_qq(monkeypatch, cap=2)

        async def send(conversation_id: str, text: str) -> bool:
            return True

        # 先送两条(用掉额度)
        for index in range(2):
            _enqueue(lane=reports_service.LANE_NORMAL, message_id=f"a{index}", conversation_id="same")
        first = await sinks_service.deliver_pending(send=send)
        assert first["delivered"] == 2

        # 再来一条: 额度已用完 → 暂缓
        _enqueue(lane=reports_service.LANE_NORMAL, message_id="b0", conversation_id="same")
        second = await sinks_service.deliver_pending(send=send)
        assert second["delivered"] == 0
        assert second["deferred"] == 1


# ---------------------------------------------------------------------------
# 与队列的协作
# ---------------------------------------------------------------------------
class TestIntegrationWithQueue:
    @pytest.mark.asyncio
    async def test_dedup_key_prevents_duplicate_notification(self, env, monkeypatch):
        """同一条消息重复入队不会导致重复通知。"""
        _enable_qq(monkeypatch)
        sent: list[str] = []

        async def send(conversation_id: str, text: str) -> bool:
            sent.append(text)
            return True

        first = _enqueue(lane=reports_service.LANE_URGENT, message_id="same-msg")
        second = _enqueue(lane=reports_service.LANE_URGENT, message_id="same-msg")
        assert second["id"] == first["id"]
        assert second["duplicated"] is True

        await sinks_service.deliver_pending(send=send)
        assert len(sent) == 1

    @pytest.mark.asyncio
    async def test_delivered_at_used_for_limiting(self, env, monkeypatch):
        """限流按"投递时刻"算(count_delivered_since 读 acked_at)。"""
        _enqueue(lane=reports_service.LANE_NORMAL)
        items = reports_service.claim(limit=5, claimed_by="tester")
        reports_service.ack(str(items[0]["id"]))

        from app.services.conversations import ensure_conversation

        conv_id = ensure_conversation("qq", "private", "conv-a")
        since = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        assert reports_service.count_delivered_since(conv_id, since) == 1
        # 一小时之前的不算
        older = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
        from app.db import get_connection

        conn = get_connection()
        conn.execute("UPDATE report_queue SET acked_at=?", (older,))
        conn.commit()
        assert reports_service.count_delivered_since(conv_id, since) == 0
