# -*- coding: utf-8 -*-
"""事实过期与冲突整理测试(app/consolidation.py)。

要守住的三件事(都是"agent 会不会拿着过期信息去回话"的直接原因):
1. **明确订正** → 旧说法被置为 superseded,不再出现在检索结果里;
2. **说法矛盾但没有订正证据** → 保留双方 + 上报队列里出现一条待确认的冲突,
   而不是让整理器猜一个;
3. **重复说法** → 合并成一条,并且整理可以反复跑而不产生重复记忆。

不联网: 总结器是注入的假实现,整理用 Doppel 的确定性整理器(零模型成本)。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app import batches
from app import consolidation
from app import memory as memory_layer
from app import reports as reports_service

pytestmark = pytest.mark.skipif(
    not memory_layer._DOPPEL_AVAILABLE,
    reason="Doppel 未安装(uv pip install -r 需要 doppel-memory)",
)


@pytest.fixture()
def env(temp_data_dir, monkeypatch):
    """独立数据目录 + 启用记忆 + 建表。"""
    monkeypatch.setenv("MEMO_ECHO_DOPPEL_ENABLED", "true")
    monkeypatch.setenv("MEMO_ECHO_OWNER_USER_ID", "owner-consol")
    monkeypatch.setenv("MEMO_ECHO_AGENT_ID", "bot-consol")
    monkeypatch.setenv("MEMO_ECHO_DOPPEL_DB_NAME", "consol.sqlite3")

    from app import config as config_module

    monkeypatch.setattr(config_module, "_settings", None, raising=False)

    from app.db import init_db

    init_db()
    yield
    memory_layer._client = None


def _conversation(external_id: str = "10001") -> dict:
    from app.services import conversations as conversations_service
    from app.services import policy as policy_service

    conversation_id = conversations_service.ensure_conversation("qq", "private", external_id)
    # 攒批阈值调到 1: 测试里一条消息就该触发总结,不必等攒够 20 条
    policy_service.update_policy(
        conversation_id, monitor=1, digest_max_messages=1, digest_window_seconds=1
    )
    return conversations_service.get_conversation(conversation_id) or {}


def _add_message(conversation_id: str, content: str, message_id: str) -> None:
    from app.services import conversations as conversations_service

    conversations_service.add_message(
        conversation_id,
        {
            "id": message_id,
            "role": "user",
            "content": content,
            "source": "inbound",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "goal_id": "",
        },
    )


class NotesSummarizer:
    """按消息内容返回预设记忆(带槽位信息)。"""

    def __init__(self, notes: list[batches.BatchNote]) -> None:
        self._notes = notes

    async def __call__(self, conversation, messages):
        return list(self._notes)


def _note(content: str, **kwargs) -> batches.BatchNote:
    return batches.BatchNote(content, actor="contact", kind="fact", **kwargs)


async def _write(
    conversation: dict,
    *,
    message_id: str,
    text: str,
    notes: list[batches.BatchNote],
) -> dict:
    """走真实攒批链路写一批记忆(并触发登记)。"""
    _add_message(conversation["id"], text, message_id)
    return await batches.run_due_batches(summarizer=NotesSummarizer(notes))


async def _facts(conversation: dict, query: str = "课表") -> list[str]:
    hits = await memory_layer.recall(conversation, query, limit=10)
    return [hit["fact"] for hit in hits]


# ---------------------------------------------------------------------------
# 明确订正: 旧说法退休
# ---------------------------------------------------------------------------
class TestCorrection:
    @pytest.mark.asyncio
    async def test_correction_retires_old_fact(self, env):
        """聊天里明说"改了" → 旧说法被置为 superseded,检索不再返回它。

        这是最要紧的一条: 课表改了以后,agent 不能再拿着"周三有课"去回话。
        """
        conversation = _conversation("20001")

        await _write(
            conversation,
            message_id="c1",
            text="km 每周三晚上有课",
            notes=[_note("km 每周三晚上有课", topic_key="课表", temporal_status="current")],
        )
        assert await _facts(conversation) == ["km 每周三晚上有课"]

        await _write(
            conversation,
            message_id="c2",
            text="课表改了，改成周五了",
            notes=[
                _note(
                    "km 的课改到周五晚上了",
                    topic_key="课表",
                    temporal_status="current",
                    revision_kind="correction",
                )
            ],
        )

        outcome = await consolidation.consolidate_conversation(conversation)
        assert outcome["ok"] is True
        assert outcome["operations"].get("correct") == 1

        facts = await _facts(conversation)
        assert facts == ["km 的课改到周五晚上了"], f"旧说法没被退休: {facts}"

    @pytest.mark.asyncio
    async def test_plain_assertion_does_not_retire(self, env):
        """没有明确订正证据时**不能**覆盖旧说法 —— 那是替号主改口供。"""
        conversation = _conversation("20002")

        await _write(
            conversation,
            message_id="p1",
            text="km 每周三晚上有课",
            notes=[_note("km 每周三晚上有课", topic_key="课表", temporal_status="current")],
        )
        await _write(
            conversation,
            message_id="p2",
            text="km 说周三有空",
            notes=[_note("km 周三有空", topic_key="课表", temporal_status="current")],
        )

        outcome = await consolidation.consolidate_conversation(conversation)
        assert outcome["operations"].get("correct", 0) == 0
        # 两条都还在(整理器选择标记冲突,而不是猜哪条对)
        facts = await _facts(conversation)
        assert len(facts) == 2, f"不该悄悄丢掉任何一条: {facts}"


# ---------------------------------------------------------------------------
# 冲突: 报给号主确认,而不是自己选一个
# ---------------------------------------------------------------------------
class TestConflict:
    @pytest.mark.asyncio
    async def test_conflict_is_marked_and_reported(self, env):
        """矛盾且无订正证据 → 写冲突标记 + 上报队列里出现待确认的冲突。"""
        conversation = _conversation("20003")

        await _write(
            conversation,
            message_id="x1",
            text="周三晚上我有空",
            notes=[_note("号主周三晚上有空", topic_key="周三安排", temporal_status="current")],
        )
        await _write(
            conversation,
            message_id="x2",
            text="周三晚上没空",
            notes=[_note("号主周三晚上没空", topic_key="周三安排", temporal_status="current")],
        )

        outcome = await consolidation.consolidate_conversation(conversation)
        assert outcome["operations"].get("conflict") == 1
        assert outcome["conflicts"], "冲突明细为空,通知里就没法说清是什么事"
        assert outcome["reported"] == 1

        questions = reports_service.list_reports(
            lane=reports_service.LANE_QUESTION, status=reports_service.STATUS_PENDING
        )
        assert len(questions) == 1
        payload = questions[0]["payload"]
        assert payload["kind"] == "memory_conflict"
        assert payload["topic_key"] == "周三安排"
        assert "不一致" in payload["summary"]

    @pytest.mark.asyncio
    async def test_conflict_reported_only_once(self, env):
        """整理是周期性跑的 —— 同一条冲突不能反复打扰号主。"""
        conversation = _conversation("20004")

        await _write(
            conversation,
            message_id="y1",
            text="周三有空",
            notes=[_note("号主周三有空", topic_key="周三安排", temporal_status="current")],
        )
        await _write(
            conversation,
            message_id="y2",
            text="周三没空",
            notes=[_note("号主周三没空", topic_key="周三安排", temporal_status="current")],
        )

        first = await consolidation.consolidate_conversation(conversation)
        second = await consolidation.consolidate_conversation(conversation)
        assert first["reported"] == 1
        assert second["reported"] == 0, "第二次整理又报了一遍同一条冲突"

        questions = reports_service.list_reports(lane=reports_service.LANE_QUESTION)
        assert len(questions) == 1

    @pytest.mark.asyncio
    async def test_both_sides_stay_active(self, env):
        """冲突双方都要保留可查 —— 丢掉任何一条都可能丢掉真相。"""
        conversation = _conversation("20005")

        await _write(
            conversation,
            message_id="z1",
            text="周三有空",
            notes=[_note("号主周三有空", topic_key="周三安排", temporal_status="current")],
        )
        await _write(
            conversation,
            message_id="z2",
            text="周三没空",
            notes=[_note("号主周三没空", topic_key="周三安排", temporal_status="current")],
        )
        await consolidation.consolidate_conversation(conversation)

        facts = await _facts(conversation, "周三")
        assert "号主周三有空" in facts and "号主周三没空" in facts


# ---------------------------------------------------------------------------
# 合并与幂等
# ---------------------------------------------------------------------------
class TestMerge:
    @pytest.mark.asyncio
    async def test_duplicates_merge(self, env):
        """同一槽位上一模一样的说法合并成一条。"""
        conversation = _conversation("20006")

        await _write(
            conversation,
            message_id="m1",
            text="km 喜欢喝冰美式",
            notes=[_note("km 喜欢喝冰美式", topic_key="口味偏好", temporal_status="current")],
        )
        await _write(
            conversation,
            message_id="m2",
            text="对，km 喜欢冰美式",
            notes=[_note("km 喜欢喝冰美式", topic_key="口味偏好", temporal_status="current")],
        )
        before = await _facts(conversation, "美式")
        assert len(before) == 2

        outcome = await consolidation.consolidate_conversation(conversation)
        assert outcome["operations"].get("merge") == 1

        after = await _facts(conversation, "美式")
        assert len(after) == 1, f"重复说法没合并: {after}"

    @pytest.mark.asyncio
    async def test_repeat_run_is_stable(self, env):
        """整理可以反复跑: 第二次不再产生动作,也不会写出重复记忆。"""
        conversation = _conversation("20007")
        await _write(
            conversation,
            message_id="r1",
            text="km 喜欢喝冰美式",
            notes=[_note("km 喜欢喝冰美式", topic_key="口味偏好", temporal_status="current")],
        )

        first = await consolidation.consolidate_conversation(conversation)
        second = await consolidation.consolidate_conversation(conversation)
        assert first["ok"] and second["ok"]
        assert sum(second["operations"].values()) == 0
        assert len(await _facts(conversation, "美式")) == 1


# ---------------------------------------------------------------------------
# 调度与检查点
# ---------------------------------------------------------------------------
class TestScheduling:
    @pytest.mark.asyncio
    async def test_checkpoint_is_persisted(self, env):
        """检查点必须落库 —— 重启后整理要能接着上一轮,而不是每次从头来。"""
        conversation = _conversation("20008")
        await _write(
            conversation,
            message_id="k1",
            text="km 喜欢喝冰美式",
            notes=[_note("km 喜欢喝冰美式", topic_key="口味偏好", temporal_status="current")],
        )

        scope = memory_layer.build_scope(conversation)
        assert consolidation.load_checkpoint(scope.scope_key) is None

        await consolidation.consolidate_conversation(conversation)

        checkpoint = consolidation.load_checkpoint(scope.scope_key)
        assert checkpoint is not None
        assert checkpoint["cycle"] >= 1

    @pytest.mark.asyncio
    async def test_run_due_respects_interval(self, env):
        """同一会话在间隔内不会被反复整理(整理是本地计算,但没必要空转)。"""
        conversation = _conversation("20009")
        await _write(
            conversation,
            message_id="d1",
            text="km 喜欢喝冰美式",
            notes=[_note("km 喜欢喝冰美式", topic_key="口味偏好", temporal_status="current")],
        )

        first = await consolidation.run_due(interval_minutes=30)
        assert first["consolidated"] == 1

        again = await consolidation.run_due(interval_minutes=30)
        assert again["consolidated"] == 0, "间隔内不该重复整理"

        later = await consolidation.run_due(
            interval_minutes=30, now=datetime.now(timezone.utc) + timedelta(minutes=31)
        )
        assert later["consolidated"] == 1, "过了间隔就该再整理一轮"

    @pytest.mark.asyncio
    async def test_scope_registered_only_after_write(self, env):
        """没写过记忆的会话不进扫描名单(没有可整理的东西)。"""
        _conversation("20010")
        summary = await consolidation.run_due()
        assert summary["scanned"] == 0


# ---------------------------------------------------------------------------
# 总结器输出的槽位信息(整理的输入)
# ---------------------------------------------------------------------------
class TestSummarizerSlots:
    def test_parse_notes_reads_slot_fields(self):
        """模型给的槽位/修订字段要能解析进来 —— 解析丢了整理就没有依据。"""
        raw = (
            '[{"content":"km 的课改到周五了","kind":"fact","actor":"contact","importance":0.8,'
            '"slot":"课表","revision":"correction","temporal":"current"}]'
        )
        notes = batches.parse_notes(raw)
        assert len(notes) == 1
        assert notes[0].topic_key == "课表"
        assert notes[0].revision_kind == "correction"
        assert notes[0].temporal_status == "current"

    def test_defaults_are_conservative(self):
        """没给字段时必须是保守默认: 不订正、不归槽位。"""
        notes = batches.parse_notes('[{"content":"随便一句","kind":"fact","actor":"contact"}]')
        assert notes[0].revision_kind == "assertion"
        assert notes[0].topic_key == ""
        assert notes[0].temporal_status == "unknown"

    def test_invalid_values_fall_back(self):
        notes = batches.parse_notes(
            '[{"content":"x","slot":"  课表  ","revision":"whatever","temporal":"someday"}]'
        )
        assert notes[0].topic_key == "课表"
        assert notes[0].revision_kind == "assertion"
        assert notes[0].temporal_status == "unknown"

    def test_kind_maps_to_memory_type(self):
        """我们自己的 kind 词表要映射到 Doppel 的类型名,否则不会被整理。"""
        note = batches.BatchNote("x", kind="relation")
        assert note.memory_type == "relationship"
        assert batches.BatchNote("x", kind="style").memory_type == "preference"
        assert batches.BatchNote("x", kind="event").memory_type == "episode"
        assert batches.BatchNote("x", kind="fact").memory_type == "fact"

    @pytest.mark.asyncio
    async def test_proposal_carries_slot_metadata(self, env):
        """槽位元数据必须真的写进记忆(字段名与 Doppel 的整理器对齐)。"""
        conversation = _conversation("20011")
        await _write(
            conversation,
            message_id="s1",
            text="km 的课改到周五了",
            notes=[
                _note(
                    "km 的课改到周五了",
                    topic_key="课表",
                    revision_kind="correction",
                    temporal_status="current",
                )
            ],
        )

        client = await memory_layer.get_client()
        assert client is not None
        page = await client.store.scan(memory_layer.build_scope(conversation), limit=10)
        record = page.records[0]
        assert record.metadata.get("topic_key") == "课表"
        assert record.metadata.get("revision_kind") == "correction"
        assert record.metadata.get("temporal_status") == "current"
        assert record.metadata.get("personal_memory_type") == "fact"

    @pytest.mark.asyncio
    async def test_slot_hint_reuses_existing_keys(self, env):
        """已有槽位名要带进提示词,让模型复用而不是另造同义词。"""
        conversation = _conversation("20012")
        await _write(
            conversation,
            message_id="h1",
            text="km 喜欢冰美式",
            notes=[_note("km 喜欢喝冰美式", topic_key="口味偏好", temporal_status="current")],
        )

        keys = await memory_layer.known_topic_keys(conversation)
        assert keys == ["口味偏好"]
        assert "口味偏好" in batches._slot_hint_prompt(keys)
        assert batches._slot_hint_prompt([]) == ""
