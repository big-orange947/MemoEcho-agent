# -*- coding: utf-8 -*-
"""上报流水线测试(规则初筛 / 队列语义 / 消费接口)。

覆盖三件事:
1. 规则初筛: 零模型成本、命中才候选(这是成本控制的第一道闸门);
2. 队列语义: 至少一次投递(租约过期重投)、死信、幂等去重、TTL 清理;
3. 复核: 批量、配额、以及**配额耗尽时退化为纯规则**(宁可多报不可漏报)。

不联网、不依赖真实 LLM。
"""
from __future__ import annotations

import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app import reports as reports_service


@pytest.fixture()
def env(temp_data_dir, monkeypatch):
    """建表并固定 bot_qq(规则 1 要靠它判断"@了我")。"""
    monkeypatch.setenv("MEMO_ECHO_BOT_QQ", "3969785168")
    from app import config as config_module

    monkeypatch.setattr(config_module, "_settings", None, raising=False)

    from app.db import init_db

    init_db()
    return reports_service


# ---------------------------------------------------------------------------
# 规则初筛
# ---------------------------------------------------------------------------
class TestRules:
    def test_no_match_for_small_talk(self, env):
        """普通寒暄不命中 —— 大多数群消息应该在这里被挡掉(零成本)。"""
        conv = {"chat_type": "group", "alert_keywords": "[]"}
        assert reports_service.evaluate_rules(conv, text="哈哈哈") == []

    def test_at_bot_matches(self, env):
        conv = {"chat_type": "group", "alert_keywords": "[]"}
        reasons = reports_service.evaluate_rules(conv, text="@3969785168 看下这个")
        assert "at_bot" in reasons

    def test_keyword_matches(self, env):
        conv = {"chat_type": "private", "alert_keywords": '["急事","改时间"]'}
        assert "keyword:改时间" in reports_service.evaluate_rules(conv, text="我们改时间吧")

    def test_keyword_accepts_plain_text(self, env):
        """关键词也允许手写成"急事,改时间"(前端/用户手填的常见形态)。"""
        conv = {"chat_type": "private", "alert_keywords": "急事,改时间"}
        assert "keyword:急事" in reports_service.evaluate_rules(conv, text="有急事找你")

    def test_question_pattern_matches(self, env):
        conv = {"chat_type": "private", "alert_keywords": "[]"}
        assert any(r.startswith("pattern:") for r in reports_service.evaluate_rules(conv, text="你几点到?"))

    def test_watchlist_matches(self, env):
        """白名单联系人: 他说的话一律候选(有些人天然重要)。"""
        from app.services import configs as configs_service

        configs_service.set_config("alert_contacts", json.dumps(["10086"], ensure_ascii=False))
        conv = {"chat_type": "private", "alert_keywords": "[]"}
        assert "watchlist" in reports_service.evaluate_rules(conv, text="到了", sender_id="10086")

    def test_keywords_do_not_crash_on_bad_json(self, env):
        conv = {"chat_type": "private", "alert_keywords": "[坏掉的"}
        assert isinstance(reports_service.evaluate_rules(conv, text="随便"), list)


# ---------------------------------------------------------------------------
# 入队与幂等
# ---------------------------------------------------------------------------
class TestEnqueue:
    def test_observe_enqueues_candidate(self, env):
        conv = {"chat_type": "group", "alert_keywords": "[]"}
        outcome = reports_service.observe(
            conv,
            conversation_id="conv-1",
            message_id="msg-1",
            text="@3969785168 帮我看下",
            sender_id="10001",
        )
        assert outcome["matched"] is True

        items = reports_service.list_reports(status=reports_service.STATUS_CANDIDATE)
        assert len(items) == 1
        assert items[0]["payload"]["reasons"]

    def test_observe_ignores_unimportant(self, env):
        conv = {"chat_type": "group", "alert_keywords": "[]"}
        outcome = reports_service.observe(
            conv, conversation_id="conv-1", message_id="msg-2", text="哈哈哈"
        )
        assert outcome["matched"] is False
        assert reports_service.list_reports(status=reports_service.STATUS_CANDIDATE) == []

    def test_duplicate_message_not_enqueued_twice(self, env):
        """同一条消息重复处理(平台重推/重启重跑)不会重复入队。"""
        conv = {"chat_type": "private", "alert_keywords": '["急"]'}
        for _ in range(2):
            reports_service.observe(
                conv, conversation_id="conv-1", message_id="msg-dup", text="有急事"
            )
        items = reports_service.list_reports(conversation_id="conv-1")
        assert len(items) == 1

    def test_high_confidence_goes_straight_to_pending(self, env):
        """判定层已经足够确定时**不等模型复核** —— 省一次调用,也省掉攒批的延迟。

        排期变更这类事早一分钟知道有意义,而确定性规则已经说清了它是急的。
        """
        conv = {"chat_type": "private", "alert_keywords": "[]"}
        outcome = reports_service.observe(
            conv,
            conversation_id="conv-auto",
            message_id="msg-auto",
            text="明天组会改到四点",
        )
        assert outcome["matched"] is True
        assert outcome["auto_promoted"] is True
        assert outcome["event"] == "schedule_change"
        assert outcome["score"] >= reports_service.AUTO_PROMOTE_SCORE

        pending = reports_service.list_reports(status=reports_service.STATUS_PENDING)
        assert len(pending) == 1
        assert pending[0]["lane"] == reports_service.LANE_URGENT
        assert pending[0]["payload"]["auto_promoted"] is True
        assert reports_service.list_candidates(limit=5) == []

    def test_medium_score_stays_candidate(self, env):
        """中等分的事件仍然走复核 —— 直通只留给高置信的那批。"""
        conv = {"chat_type": "private", "alert_keywords": "[]"}
        outcome = reports_service.observe(
            conv, conversation_id="conv-mid", message_id="msg-mid", text="借我50块钱"
        )
        assert outcome["matched"] is True
        assert outcome["auto_promoted"] is False
        assert reports_service.list_candidates(limit=5)

    def test_same_event_merges_into_cluster(self, env):
        """同类事件在窗口内合并成一条 —— 群里同一件事说三遍,不该报三条。"""
        conv = {"chat_type": "group", "alert_keywords": "[]"}
        first = reports_service.observe(
            conv, conversation_id="conv-cl", message_id="c1", text="明天组会改到四点"
        )
        second = reports_service.observe(
            conv, conversation_id="conv-cl", message_id="c2", text="改到四点对吧"
        )
        third = reports_service.observe(
            conv, conversation_id="conv-cl", message_id="c3", text="@3969785168 看下这个"
        )

        assert first.get("merged") is not True
        assert second.get("merged") is True
        assert third.get("merged") is not True, "不同类事件不该被并进同一条"

        items = reports_service.list_reports(conversation_id="conv-cl")
        assert len(items) == 2
        merged = [
            item for item in items if (item.get("payload") or {}).get("cluster_count")
        ]
        assert len(merged) == 1
        assert merged[0]["payload"]["cluster_count"] == 2
        assert merged[0]["payload"]["text"] == "改到四点对吧"
        assert merged[0]["payload"]["event"] == "schedule_change"

    def test_burst_escalates_quiet_message(self, env):
        """对方连发几条没人回 → 一条普通的"在吗"也值得知道。"""
        conv = {"chat_type": "private", "alert_keywords": "[]"}
        from app.services import conversations as conversations_service

        conversation_id = conversations_service.ensure_conversation("qq", "private", "10001")
        for index in range(3):
            conversations_service.add_message(
                conversation_id,
                {
                    "id": f"bf-{index}",
                    "role": "user",
                    "content": "在吗",
                    "source": "inbound",
                    "created_at": "",
                    "goal_id": "",
                },
            )
        outcome = reports_service.observe(
            conv,
            conversation_id=conversation_id,
            message_id="bf-3",
            text="在吗",
            sender_id="10001",
        )
        assert outcome["matched"] is True
        assert outcome["event"] == "burst"
        assert outcome["score"] >= 0.55

    @pytest.mark.asyncio
    async def test_time_advance_escalates_pending(self, env):
        """异步流水线才做得到的一件事: 事情临近了,压在队里的通知自己变急。

        早上收到的"今晚七点改到八点"是个通知;到傍晚还压着就是"马上要发生"。
        """
        from datetime import datetime, timezone

        conv = {"chat_type": "private", "alert_keywords": "[]"}
        outcome = reports_service.observe(
            conv,
            conversation_id="conv-time",
            message_id="t1",
            text="明天下午三点见",
        )
        assert outcome["lane"] == reports_service.LANE_NORMAL
        # 候选先按规则放行(不注入复核器 = 退化路径,沿用判定层的通道)
        await reports_service.flush_candidates(reviewer=None, max_age_seconds=0)

        # 把这条记录的 due_at 改到 30 分钟后,模拟时间流逝
        from app.db import get_connection

        conn = get_connection()
        row = conn.execute(
            "SELECT id, payload FROM report_queue WHERE conversation_id='conv-time'"
        ).fetchone()
        payload = json.loads(row["payload"])
        assert payload.get("due_at"), "判定层应把解析出的时间写进 payload"
        payload["due_at"] = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
        conn.execute(
            "UPDATE report_queue SET payload=? WHERE id=?",
            (json.dumps(payload, ensure_ascii=False), row["id"]),
        )
        conn.commit()

        result = reports_service.rescore_pending()
        assert result["escalated"] == 1
        updated = reports_service.get_report(row["id"])
        assert updated["lane"] == reports_service.LANE_URGENT
        assert updated["payload"]["escalated"] == "due_soon"

    @pytest.mark.asyncio
    async def test_time_advance_marks_stale(self, env):
        """事情已经过去了: 打标,不丢 —— 它可能正是"已经发生"的重要信息。"""
        from datetime import datetime, timezone

        conv = {"chat_type": "private", "alert_keywords": "[]"}
        reports_service.observe(
            conv, conversation_id="conv-stale", message_id="s1", text="明天下午三点见"
        )
        await reports_service.flush_candidates(reviewer=None, max_age_seconds=0)

        from app.db import get_connection

        conn = get_connection()
        row = conn.execute(
            "SELECT id, payload FROM report_queue WHERE conversation_id='conv-stale'"
        ).fetchone()
        payload = json.loads(row["payload"])
        payload["due_at"] = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
        conn.execute(
            "UPDATE report_queue SET payload=? WHERE id=?",
            (json.dumps(payload, ensure_ascii=False), row["id"]),
        )
        conn.commit()

        result = reports_service.rescore_pending()
        assert result["stale"] == 1
        updated = reports_service.get_report(row["id"])
        assert updated["payload"]["stale"] is True
        assert updated["lane"] == reports_service.LANE_NORMAL, "过期不等于要升级为紧急"


# ---------------------------------------------------------------------------
# 队列语义(至少一次 / 死信 / TTL)
# ---------------------------------------------------------------------------
class TestQueueSemantics:
    def _enqueue_pending(self, **kwargs):
        return reports_service.enqueue(
            lane=kwargs.pop("lane", reports_service.LANE_URGENT),
            conversation_id="conv-q",
            message_ids=["m1"],
            payload={"summary": "内容"},
            dedup_key=kwargs.pop("dedup_key", "m1"),
            status=reports_service.STATUS_PENDING,
            **kwargs,
        )

    def test_claim_then_ack(self, env):
        self._enqueue_pending()
        items = reports_service.claim(limit=5, claimed_by="tester")
        assert len(items) == 1
        assert items[0]["status"] == reports_service.STATUS_CLAIMED
        assert items[0]["claimed_by"] == "tester"

        assert reports_service.ack(items[0]["id"]) is True
        assert reports_service.stats().get(reports_service.STATUS_ACKED) == 1

    def test_claimed_item_not_handed_out_again(self, env):
        """已被认领(租约内)的记录,不会被第二次认领 —— 避免重复通知。"""
        self._enqueue_pending()
        first = reports_service.claim(limit=5)
        second = reports_service.claim(limit=5)
        assert len(first) == 1 and second == []

    def test_expired_lease_returns_to_queue(self, env):
        """上游认领后崩了: 租约过期,消息回到队列(至少一次的关键)。"""
        self._enqueue_pending()
        items = reports_service.claim(limit=5, lease_seconds=1)
        assert len(items) == 1

        # 手动把租约改成过去时间,模拟"上游再也没回来"
        from app.db import get_connection

        conn = get_connection()
        conn.execute(
            "UPDATE report_queue SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE id=?",
            (items[0]["id"],),
        )
        conn.commit()

        again = reports_service.claim(limit=5)
        assert len(again) == 1, "过期租约未回队列"
        assert again[0]["attempts"] == 2

    def test_repeated_failures_go_to_dead_letter(self, env):
        """反复认领失败(上游处理不了)的进死信,不再无限重投。"""
        self._enqueue_pending()
        from app.db import get_connection

        conn = get_connection()
        for _ in range(10):
            reports_service.claim(limit=5, lease_seconds=1)
            conn.execute(
                "UPDATE report_queue SET lease_expires_at='2000-01-01T00:00:00+00:00'"
                " WHERE status='claimed'"
            )
            conn.commit()
            reports_service.reap_expired()

        assert reports_service.stats().get(reports_service.STATUS_DEAD) == 1

    def test_drop(self, env):
        self._enqueue_pending()
        item = reports_service.claim(limit=5)[0]
        assert reports_service.drop(item["id"], reason="不重要") is True
        assert reports_service.stats().get(reports_service.STATUS_DROPPED) == 1

    def test_lane_filter(self, env):
        self._enqueue_pending(dedup_key="a", lane=reports_service.LANE_URGENT)
        self._enqueue_pending(dedup_key="b", lane=reports_service.LANE_NORMAL)

        urgent = reports_service.claim(limit=5, lane=reports_service.LANE_URGENT)
        assert len(urgent) == 1
        assert urgent[0]["lane"] == reports_service.LANE_URGENT

    def test_purge_resolved(self, env):
        """已处理的记录超期清理(队列不无限增长)。"""
        self._enqueue_pending()
        item = reports_service.claim(limit=5)[0]
        reports_service.ack(item["id"])
        # retention_hours 下限保护为 1 小时,这里用过去时间戳验证删除逻辑
        from app.db import get_connection

        conn = get_connection()
        conn.execute("UPDATE report_queue SET updated_at='2000-01-01T00:00:00+00:00'")
        conn.commit()
        assert reports_service.purge_resolved() == 1


# ---------------------------------------------------------------------------
# 快模型复核
# ---------------------------------------------------------------------------
class TestReview:
    def _make_candidate(self, text: str = "在吗，想问个事情", message_id: str = "m1"):
        """造一条**停留在候选**的记录(供复核相关用例使用)。

        注意别用"急事找你"这类文本: 判定层现在会给它高分并**直接放行**
        (见 AUTO_PROMOTE_SCORE),根本不会停在候选 ——
        那样测的就不是复核了。这里用一条中等分的事件(疑问)。
        """
        conv = {"chat_type": "private", "alert_keywords": '["急"]'}
        return reports_service.observe(
            conv, conversation_id="conv-r", message_id=message_id, text=text
        )

    @pytest.mark.asyncio
    async def test_review_promotes_candidate(self, env):
        """复核后候选被提升为 pending,并带上模型给的摘要与 lane。"""
        self._make_candidate()
        candidate = reports_service.list_candidates(limit=5)[0]

        async def reviewer(items):
            return [{"id": items[0]["id"], "lane": "urgent", "summary": "对方说有急事"}]

        result = await reports_service.flush_candidates(reviewer=reviewer, max_age_seconds=0)
        assert result["promoted"] == 1

        pending = reports_service.list_reports(status=reports_service.STATUS_PENDING)
        assert len(pending) == 1
        assert pending[0]["lane"] == "urgent"
        assert pending[0]["payload"]["summary"] == "对方说有急事"
        assert candidate["id"] == pending[0]["id"]

    @pytest.mark.asyncio
    async def test_waits_when_batch_not_full(self, env):
        """候选没攒够且还很新 → 先攒着(省一次模型调用)。"""
        self._make_candidate()

        async def reviewer(items):  # pragma: no cover - 不该被调用
            raise AssertionError("不该在未攒够时调用模型")

        result = await reports_service.flush_candidates(reviewer=reviewer, limit=5, max_age_seconds=600)
        assert result.get("waiting") is True
        assert reports_service.stats().get(reports_service.STATUS_CANDIDATE) == 1

    @pytest.mark.asyncio
    async def test_quota_exhausted_degrades_to_rules(self, env):
        """配额用尽 → 不调模型,但候选照常上报(宁可多报,不可漏报)。"""
        self._make_candidate()

        async def reviewer(items):  # pragma: no cover - 配额耗尽时不该被调用
            raise AssertionError("配额耗尽时不该调用模型")

        # 把当日用量直接顶到上限
        from app.services import configs as configs_service

        configs_service.set_config(reports_service._usage_key(), "999")

        result = await reports_service.flush_candidates(reviewer=reviewer, max_age_seconds=0)
        assert result["degraded"] == "quota_exhausted"
        pending = reports_service.list_reports(status=reports_service.STATUS_PENDING)
        assert len(pending) == 1
        assert pending[0]["payload"]["degraded"] == "quota_exhausted"

    @pytest.mark.asyncio
    async def test_reviewer_failure_degrades_without_losing_message(self, env):
        """复核抛异常 → 退化为纯规则,消息仍然上报(不能因为模型抖动丢消息)。"""
        self._make_candidate()

        async def broken_reviewer(items):
            raise RuntimeError("模型超时")

        result = await reports_service.flush_candidates(reviewer=broken_reviewer, max_age_seconds=0)
        assert result["degraded"].startswith("review_error")
        assert reports_service.stats().get(reports_service.STATUS_PENDING) == 1


# ---------------------------------------------------------------------------
# HTTP 接口(上游 agent 的消费路径)
# ---------------------------------------------------------------------------
@pytest.fixture()
def client(temp_data_dir, monkeypatch):
    monkeypatch.setenv("MEMO_ECHO_API_TOKEN", "")
    monkeypatch.setenv("MEMO_ECHO_BOT_QQ", "3969785168")
    monkeypatch.setenv("MEMO_ECHO_WEBHOOK_ASYNC", "false")

    from app import config as config_module

    monkeypatch.setattr(config_module, "_settings", None, raising=False)

    import app.main as main_module

    app = main_module.create_app()
    with TestClient(app) as test_client:
        yield test_client


class TestReportsApi:
    def test_claim_ack_flow(self, client):
        """上游消费全流程: 认领 → 处理 → 确认。"""
        reports_service.enqueue(
            lane=reports_service.LANE_URGENT,
            conversation_id="conv-api",
            message_ids=["m1"],
            payload={"summary": "有人找你"},
            dedup_key="api-1",
            status=reports_service.STATUS_PENDING,
        )

        claimed = client.post("/api/reports/claim", json={"limit": 5, "claimed_by": "main-agent"}).json()
        assert claimed["count"] == 1
        record = claimed["items"][0]
        assert record["claimed_by"] == "main-agent"

        acked = client.post(f"/api/reports/{record['id']}/ack", json={}).json()
        assert acked["ok"] is True

        assert client.get("/api/reports/stats").json().get("acked") == 1

    def test_drop_flow(self, client):
        reports_service.enqueue(
            lane=reports_service.LANE_NORMAL,
            conversation_id="conv-api",
            message_ids=["m2"],
            payload={},
            dedup_key="api-2",
            status=reports_service.STATUS_PENDING,
        )
        record = client.post("/api/reports/claim", json={}).json()["items"][0]
        assert client.post(f"/api/reports/{record['id']}/drop", json={"reason": "不重要"}).json()["ok"] is True

    def test_list_and_filter(self, client):
        reports_service.enqueue(
            lane=reports_service.LANE_QUESTION,
            conversation_id="conv-q",
            message_ids=["q1"],
            payload={"summary": "问你个事"},
            dedup_key="q-1",
            status=reports_service.STATUS_PENDING,
        )
        items = client.get("/api/reports", params={"lane": "question"}).json()
        assert len(items) == 1
        assert items[0]["message_ids"] == ["q1"]

    def test_ack_missing_returns_404(self, client):
        assert client.post("/api/reports/no-such/ack", json={}).status_code == 404

    def test_subscribe_returns_immediately_when_queued(self, client):
        """长轮询: 队列里有货就立刻返回(不用等超时)。"""
        reports_service.enqueue(
            lane=reports_service.LANE_URGENT,
            conversation_id="conv-sub",
            message_ids=["s1"],
            payload={},
            dedup_key="s-1",
            status=reports_service.STATUS_PENDING,
        )
        resp = client.get("/api/reports/subscribe", params={"timeout": 1})
        assert resp.status_code == 200
        assert resp.json()["count"] == 1
