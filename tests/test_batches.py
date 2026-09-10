# -*- coding: utf-8 -*-
"""攒批记忆测试(app/batches.py)。

背景: 长期记忆从"逐条写入"改为"攒批总结"(逐条写会把记忆碎成一堆"嗯""好的"),
所以这里覆盖的不是"能不能写",而是:
- 触发条件: 满条数 / 空闲超时 / 都不满足 / 没开监视;
- 游标推进与幂等: 同一条消息不会被总结两次;
- 总结返回空(没价值)时不写记忆,但游标**必须**推进(否则这批消息会永久卡住);
- 失败降级: 总结器报错时不推进水位线,修好后重试仍能覆盖同一批;
- 端到端: run_due_batches 之后能用 Doppel 的 recall 召回总结出来的记忆;
- 回归: ingest / finalize 不再逐条写记忆。

不联网: 总结器(Summarizer)是可注入接口,测试全部注入假实现。
Doppel 未安装时,依赖它的用例跳过(参考 tests/test_memory.py)。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app import batches
from app import memory as memory_layer

# 依赖 Doppel 的用例统一打这个标记(纯宿主侧逻辑的用例不受影响)
needs_doppel = pytest.mark.skipif(
    not memory_layer._DOPPEL_AVAILABLE,
    reason="Doppel 未安装(uv pip install -e D:\\project\\Doppel)",
)


# ---------------------------------------------------------------------------
# 夹具与工具
# ---------------------------------------------------------------------------
@pytest.fixture()
def batch_env(temp_data_dir, monkeypatch):
    """独立数据目录 + 启用记忆 + 固定的号主/机器人标识。

    与 tests/test_memory.py 的 memory_env 同一套路: Doppel 客户端是模块级单例,
    数据目录每个测试都换,所以进入测试前必须先把它清掉(否则会连到上一个
    测试的库文件),测试结束后再清一次,避免污染其它测试。
    """
    monkeypatch.setenv("MEMO_ECHO_DOPPEL_ENABLED", "true")
    monkeypatch.setenv("MEMO_ECHO_OWNER_USER_ID", "owner-batch")
    monkeypatch.setenv("MEMO_ECHO_AGENT_ID", "bot-batch")
    monkeypatch.setenv("MEMO_ECHO_DOPPEL_DB_NAME", "mem-batch.sqlite3")

    from app import config as config_module

    monkeypatch.setattr(config_module, "_settings", None, raising=False)
    memory_layer._client = None

    from app.db import init_db

    init_db()

    yield

    memory_layer._client = None


def _now(offset_seconds: float = 0) -> str:
    """当前 UTC 时间(可偏移),格式与 recorder/conversations 写入的一致。"""
    return (datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)).isoformat()


def _now_dt() -> datetime:
    """当前 UTC 时间(带时区,用于 is_due 的 now 参数)。"""
    return datetime.now(timezone.utc)


def _make_conversation(
    external_id: str = "10001",
    *,
    monitor: int = 1,
    digest_max_messages: int = 20,
    digest_window_seconds: int = 1800,
    platform: str = "qq",
    chat_type: str = "private",
) -> dict:
    """建一个会话并设置策略,返回会话行(攒批的所有判定都挂在会话上)。"""
    from app.services import conversations as conversations_service
    from app.services import policy as policy_service

    conversation_id = conversations_service.ensure_conversation(platform, chat_type, external_id)
    policy_service.update_policy(
        conversation_id,
        monitor=monitor,
        digest_max_messages=digest_max_messages,
        digest_window_seconds=digest_window_seconds,
    )
    return conversations_service.get_conversation(conversation_id) or {}


def _add_message(
    conversation_id: str,
    content: str,
    *,
    message_id: str = "",
    role: str = "user",
    source: str = "inbound",
    at: str | None = None,
) -> str:
    """往会话里写一条消息(显式给时间,才能控制水位线与空闲判定)。"""
    from app.services import conversations as conversations_service

    return conversations_service.add_message(
        conversation_id,
        {
            "id": message_id or None,
            "role": role,
            "content": content,
            "source": source,
            "created_at": at or _now(),
            "goal_id": "",
        },
    )


class FakeSummarizer:
    """可注入的假总结器: 记录"看到哪些消息",返回预设记忆(测试不联网)。

    build 缺省时返回一条"要点: <最后一条消息>"的记忆 —— 这样断言 calls 就能
    看出每一轮到底总结了哪几条消息(幂等/游标推进的关键证据)。
    """

    def __init__(self, build=None, error: Exception | None = None) -> None:
        self._build = build or (
            lambda messages: [batches.BatchNote(f"要点: {messages[-1]['content']}")]
        )
        self._error = error
        self.calls: list[list[dict]] = []

    async def __call__(self, conversation, messages):
        self.calls.append([dict(message) for message in messages])
        if self._error is not None:
            raise self._error
        return list(self._build(list(messages)))

    @property
    def seen_ids(self) -> list[list[str]]:
        """每轮总结到的消息 ID(断言"同一条消息不会被总结两次")。"""
        return [[str(message["id"]) for message in call] for call in self.calls]


async def _recall(conversation: dict, query: str) -> list:
    """直接问 Doppel 要记忆(不带降级,失败就是失败)。"""
    client = await memory_layer.get_client()
    assert client is not None
    return await client.recall(query, [memory_layer.build_scope(conversation)])


# ---------------------------------------------------------------------------
# 触发条件
# ---------------------------------------------------------------------------
class TestTrigger:
    def test_not_due_without_pending(self, batch_env):
        """没有待处理消息: 不触发(没什么可总结的)。"""
        conversation = _make_conversation(monitor=1, digest_max_messages=3)
        due, reason = batches.is_due(conversation, {}, _now_dt())
        assert due is False
        assert reason == "no-pending"

    def test_due_when_count_reached(self, batch_env):
        """攒够 digest_max_messages 条: 触发(不必等对方停下来)。"""
        conversation = _make_conversation(monitor=1, digest_max_messages=3)
        for index in range(3):
            _add_message(conversation["id"], f"消息{index}")

        due, reason = batches.is_due(conversation, {}, _now_dt())
        assert due is True
        assert reason.startswith("count>=")

    def test_not_due_when_below_count_and_recent(self, batch_env):
        """没攒够、对方还在说: 再等等(这是"攒批"的本意)。"""
        conversation = _make_conversation(monitor=1, digest_max_messages=3)
        _add_message(conversation["id"], "刚说了一句")

        due, reason = batches.is_due(conversation, {}, _now_dt())
        assert due is False
        assert reason == "waiting"

    def test_due_when_idle(self, batch_env):
        """消息已静止超过 digest_window_seconds: 触发(对方不说话了,收尾)。"""
        conversation = _make_conversation(
            monitor=1, digest_max_messages=10, digest_window_seconds=1800
        )
        last_at = _now(-3600)  # 一小时前
        _add_message(conversation["id"], "聊完就散了", at=last_at)
        _add_message(conversation["id"], "最后一句", at=last_at)

        due, reason = batches.is_due(conversation, {}, _now_dt())
        assert due is True
        assert reason.startswith("idle>=")

    def test_not_due_when_idle_but_processed(self, batch_env):
        """空闲但已处理过(水位线追平): 不触发 —— 否则会反复重总结同一批。"""
        conversation = _make_conversation(
            monitor=1, digest_max_messages=10, digest_window_seconds=1800
        )
        last_at = _now(-3600)
        _add_message(conversation["id"], "已经总结过了", at=last_at)

        due, reason = batches.is_due(conversation, {"last_message_at": last_at}, _now_dt())
        assert due is False
        assert reason == "no-pending"

    def test_unmonitored_conversation_not_due(self, batch_env):
        """没开 monitor 的会话不参与攒批(策略总开关)。"""
        conversation = _make_conversation(
            monitor=0, digest_max_messages=1, digest_window_seconds=1
        )
        _add_message(conversation["id"], "没人监视这句话", at=_now(-3600))

        due, reason = batches.is_due(conversation, {}, _now_dt())
        assert due is False
        assert reason == "not-monitored"

    @needs_doppel
    @pytest.mark.asyncio
    async def test_run_due_batches_skips_unmonitored(self, batch_env):
        """run_due_batches 只扫监视中的会话(未监视的连总结器都不会碰)。"""
        watched = _make_conversation("10011", monitor=1, digest_max_messages=1)
        ignored = _make_conversation("10012", monitor=0, digest_max_messages=1)
        _add_message(watched["id"], "监视中的消息")
        _add_message(ignored["id"], "未监视的消息")

        summarizer = FakeSummarizer()
        summary = await batches.run_due_batches(summarizer=summarizer)
        assert [run["conversation_id"] for run in summary["runs"]] == [watched["id"]]
        assert summary["written"] == 1


# ---------------------------------------------------------------------------
# 游标推进与幂等
# ---------------------------------------------------------------------------
@needs_doppel
class TestCursorAndIdempotency:
    @pytest.mark.asyncio
    async def test_first_run_writes_and_advances(self, batch_env):
        """第一轮: 攒够条数 → 总结 → 写入记忆 → 游标与水位线一起推进。"""
        conversation = _make_conversation("20001", monitor=1, digest_max_messages=3)
        for index in range(3):
            _add_message(conversation["id"], f"第{index}条", message_id=f"g{index}")

        summarizer = FakeSummarizer()
        summary = await batches.run_due_batches(summarizer=summarizer)

        assert summary["enabled"] is True
        assert [run["conversation_id"] for run in summary["runs"]] == [conversation["id"]]
        assert summary["written"] == 1
        assert summarizer.seen_ids == [["g0", "g1", "g2"]]
        # 总结器看到的是"带角色与时间"的消息(raw 必须能穿过 Doppel 的页校验)
        first = summarizer.calls[0][0]
        assert first["role"] == "user" and first["source"] == "inbound"
        assert first["actor"] == "contact" and first["created_at"]

        progress = batches.load_progress(conversation["id"])
        assert progress["last_status"] == "ok"
        assert progress["cursor"], "游标必须推进,否则这批消息会被反复总结"
        assert progress["last_message_at"] != ""
        # 已追平: 下一次扫描不再触发
        due, reason = batches.is_due(conversation, progress, _now_dt())
        assert (due, reason) == (False, "no-pending")

    @pytest.mark.asyncio
    async def test_same_message_never_summarized_twice(self, batch_env):
        """同一个会话分两轮: 第二轮只总结新消息,旧消息不再进总结器。"""
        conversation = _make_conversation("20002", monitor=1, digest_max_messages=2)
        _add_message(conversation["id"], "旧消息一", message_id="o1")
        _add_message(conversation["id"], "旧消息二", message_id="o2")

        summarizer = FakeSummarizer()
        await batches.run_due_batches(summarizer=summarizer)
        assert summarizer.seen_ids == [["o1", "o2"]]

        # 再来两条新消息
        _add_message(conversation["id"], "新消息一", message_id="n1")
        _add_message(conversation["id"], "新消息二", message_id="n2")

        summary = await batches.run_due_batches(summarizer=summarizer)
        assert summary["written"] == 1
        assert summarizer.seen_ids == [["o1", "o2"], ["n1", "n2"]]

    @pytest.mark.asyncio
    async def test_no_new_messages_no_run(self, batch_env):
        """没有新消息时连总结器都不会被调用(不花 LLM 的钱)。"""
        conversation = _make_conversation("20003", monitor=1, digest_max_messages=1)
        _add_message(conversation["id"], "只有这一条")

        summarizer = FakeSummarizer()
        await batches.run_due_batches(summarizer=summarizer)
        summary = await batches.run_due_batches(summarizer=summarizer)

        assert summary["runs"] == []
        assert summary["written"] == 0
        assert len(summarizer.calls) == 1

    @needs_doppel
    @pytest.mark.asyncio
    async def test_multi_page_batch_is_one_summary(self, batch_env, monkeypatch):
        """跨多页的一批消息 = 一次总结(分页是读取细节,不该变成多次写入)。"""
        monkeypatch.setattr(batches, "PAGE_SIZE", 2)
        conversation = _make_conversation("20007", monitor=1, digest_max_messages=5)
        ids = [
            _add_message(conversation["id"], f"第{index}条", message_id=f"p{index}")
            for index in range(5)
        ]

        summarizer = FakeSummarizer()
        summary = await batches.run_due_batches(summarizer=summarizer)

        assert summarizer.seen_ids == [ids]
        assert summary["written"] == 1
        assert summary["runs"][0]["messages"] == 5

    @needs_doppel
    @pytest.mark.asyncio
    async def test_run_cap_does_not_skip_messages(self, batch_env, monkeypatch):
        """单轮上限: 超出上限的消息留到下一轮,**不能被游标越过**(越过就永久丢失)。

        这是分页与"单轮条数上限"配合处最容易写错的地方: 若游标跟着
        "页读到的最后一条"走而不是"实际总结的最后一条",被截断的消息就没了。
        """
        monkeypatch.setattr(batches, "MAX_MESSAGES_PER_RUN", 2)
        conversation = _make_conversation("20006", monitor=1, digest_max_messages=2)
        # 时间显式分开: 水位线比较用的是消息时间文本,同一时刻的消息会让
        # "还有没有待处理"变得不确定(生产里由 recorder 保证单调递增)
        ids = [
            _add_message(
                conversation["id"],
                f"第{index}条",
                message_id=f"c{index}",
                at=_now(-600 + index * 10),
            )
            for index in range(4)
        ]

        summarizer = FakeSummarizer()
        first = await batches.run_due_batches(summarizer=summarizer)
        second = await batches.run_due_batches(summarizer=summarizer)
        third = await batches.run_due_batches(summarizer=summarizer)

        assert first["written"] == 1 and second["written"] == 1
        assert third["runs"] == []          # 全部处理完,不再触发
        assert summarizer.seen_ids == [ids[:2], ids[2:]]

    @pytest.mark.asyncio
    async def test_reader_pages_advance_and_match_contract(self, batch_env):
        """读取器分页: 不重不漏、游标严格推进、满足 Doppel 的分页契约。

        直接套上 Doppel 的 GuardedHistoryReader —— 它会把"非空页必须给出
        变化过的 next_cursor""has_more 必须带消息""单页不得超过 limit"
        这些契约逐条校验,校验不通过就抛 HistoryReaderContractError。
        """
        from doppel_memory import GuardedHistoryReader

        conversation = _make_conversation("20004", monitor=1)
        same_moment = _now()  # 刻意让 5 条消息时间完全相同,逼出复合游标的兜底
        ids = [
            _add_message(conversation["id"], f"消息{index}", message_id=f"m{index}", at=same_moment)
            for index in range(5)
        ]

        reader = GuardedHistoryReader(batches.MessageHistoryReader(conversation, page_size=2))
        seen: list[str] = []
        cursor = ""
        for _ in range(10):
            page = await reader.read(cursor=cursor, limit=2)
            if not page.messages:
                break
            assert page.next_cursor and page.next_cursor != cursor
            assert len(page.messages) <= 2
            seen.extend(message.message_id for message in page.messages)
            cursor = page.next_cursor
            if not page.has_more:
                break

        assert seen == ids

    @pytest.mark.asyncio
    async def test_reader_filters_by_actor(self, batch_env):
        """actor 过滤发生在 SQL 里(先过滤再分页,游标才不会被过滤掉的行卡住)。"""
        conversation = _make_conversation("20005", monitor=1)
        _add_message(conversation["id"], "对方说", message_id="c1", role="user", source="inbound")
        _add_message(conversation["id"], "我说的", message_id="a1", role="assistant", source="outbound")
        _add_message(conversation["id"], "系统记录", message_id="s1", role="system", source="system")

        reader = batches.MessageHistoryReader(conversation, page_size=10)
        page = await reader.read(cursor="", limit=10, actors={"agent"})
        assert [message.message_id for message in page.messages] == ["a1"]
        assert page.messages[0].actor == "agent"


# ---------------------------------------------------------------------------
# 总结为空(没价值就不写)
# ---------------------------------------------------------------------------
@needs_doppel
class TestEmptySummary:
    @pytest.mark.asyncio
    async def test_empty_notes_write_nothing_but_advance_cursor(self, batch_env):
        """攒的这批"没价值"(总结返回空): 不写记忆,但游标必须推进。"""
        conversation = _make_conversation("30001", monitor=1, digest_max_messages=2)
        _add_message(conversation["id"], "嗯", message_id="e1")
        _add_message(conversation["id"], "好的", message_id="e2")

        summarizer = FakeSummarizer(build=lambda messages: [])
        summary = await batches.run_due_batches(summarizer=summarizer)

        assert summary["written"] == 0
        assert summary["runs"][0]["status"] == "empty"

        progress = batches.load_progress(conversation["id"])
        assert progress["cursor"], "空结果也要推进游标,否则这批消息永远卡住"
        assert progress["last_message_at"]
        assert batches.is_due(conversation, progress, _now_dt())[0] is False

        # 而且**真的**没写记忆
        assert await _recall(conversation, "嗯") == []
        assert await _recall(conversation, "好的") == []

        # 再扫一轮: 不会被重复总结
        assert (await batches.run_due_batches(summarizer=summarizer))["runs"] == []
        assert len(summarizer.calls) == 1

    @pytest.mark.asyncio
    async def test_notes_are_stored_as_one_batch(self, batch_env):
        """一批多条记忆: 一起写入,且都带 processor(可追溯是谁写的)。"""
        conversation = _make_conversation("30002", monitor=1, digest_max_messages=2)
        _add_message(conversation["id"], "我喜欢喝美式", message_id="d1")
        _add_message(conversation["id"], "每周三晚上有课", message_id="d2")

        summarizer = FakeSummarizer(
            build=lambda messages: [
                batches.BatchNote("小号喜欢喝美式咖啡", kind="fact", actor="contact", importance=0.8),
                batches.BatchNote("小号每周三晚上有课", kind="fact", actor="contact", importance=0.7),
            ]
        )
        summary = await batches.run_due_batches(summarizer=summarizer)
        assert summary["written"] == 2

        hits = await _recall(conversation, "美式")
        assert hits and hits[0].extractor == batches.TASK_NAME


# ---------------------------------------------------------------------------
# 失败降级
# ---------------------------------------------------------------------------
@needs_doppel
class TestFailure:
    @pytest.mark.asyncio
    async def test_summarizer_error_keeps_watermark(self, batch_env):
        """总结器报错: 不推进水位线(下次重读同一批),也不写任何记忆。"""
        conversation = _make_conversation("40001", monitor=1, digest_max_messages=2)
        _add_message(conversation["id"], "第一条", message_id="f1")
        _add_message(conversation["id"], "第二条", message_id="f2")

        broken = FakeSummarizer(error=RuntimeError("模型超时"))
        summary = await batches.run_due_batches(summarizer=broken)

        assert summary["runs"][0]["status"] == "error"
        assert summary["written"] == 0
        progress = batches.load_progress(conversation["id"])
        assert progress["cursor"] == ""
        assert progress["last_message_at"] == ""
        assert progress["last_status"] == "error"
        assert "模型超时" in progress["last_error"]
        assert await _recall(conversation, "第一条") == []

        # 修好后重试: 同一批消息仍然会被总结(至少一次语义)
        healthy = FakeSummarizer()
        summary = await batches.run_due_batches(summarizer=healthy)
        assert summary["written"] == 1
        assert healthy.seen_ids == [["f1", "f2"]]

    @pytest.mark.asyncio
    async def test_broken_reader_does_not_block_other_conversations(self, batch_env, monkeypatch):
        """单个会话的读取出错,不影响其它会话的攒批(扫描不整体中断)。"""
        broken_conv = _make_conversation("40002", monitor=1, digest_max_messages=1)
        healthy_conv = _make_conversation("40003", monitor=1, digest_max_messages=1)
        _add_message(broken_conv["id"], "这条会炸", message_id="b1")
        _add_message(healthy_conv["id"], "这条正常", message_id="h1")

        original_read = batches.MessageHistoryReader.read

        async def flaky_read(self, **kwargs):
            if self.conversation_id == broken_conv["id"]:
                raise RuntimeError("读库炸了")
            return await original_read(self, **kwargs)

        monkeypatch.setattr(batches.MessageHistoryReader, "read", flaky_read)

        summary = await batches.run_due_batches(summarizer=FakeSummarizer())
        statuses = {run["conversation_id"]: run["status"] for run in summary["runs"]}
        assert statuses[broken_conv["id"]] == "error"
        assert statuses[healthy_conv["id"]] == "ok"


# ---------------------------------------------------------------------------
# 端到端(Doppel 真实写入 + 召回)
# ---------------------------------------------------------------------------
@needs_doppel
class TestEndToEnd:
    @pytest.mark.asyncio
    async def test_run_due_batches_then_recall(self, batch_env):
        """端到端: 攒批总结写进 Doppel,之后能 recall 出来。"""
        conversation = _make_conversation("50001", monitor=1, digest_max_messages=3)
        _add_message(conversation["id"], "你记一下,我下周三要去北京出差", message_id="k1")
        _add_message(conversation["id"], "好的", message_id="k2")
        _add_message(conversation["id"], "要待三天", message_id="k3")

        summarizer = FakeSummarizer(
            build=lambda messages: [
                batches.BatchNote("小号下周三要去北京出差,待三天", kind="fact", actor="contact", importance=0.9)
            ]
        )
        summary = await batches.run_due_batches(summarizer=summarizer)

        assert summary["written"] == 1
        hits = await _recall(conversation, "北京")
        assert hits, "攒批写入的记忆应能被召回"
        assert any("北京" in hit.fact for hit in hits)
        assert hits[0].extractor == batches.TASK_NAME

    @pytest.mark.asyncio
    async def test_memory_is_isolated_per_conversation(self, batch_env):
        """攒批同样遵守 scope 隔离: 别的联系人的记忆里看不到这条。"""
        conversation = _make_conversation("50002", monitor=1, digest_max_messages=1)
        other = _make_conversation("50003", monitor=1, digest_max_messages=1)
        _add_message(conversation["id"], "我的生日是三月五号", message_id="i1")

        await batches.run_due_batches(
            summarizer=FakeSummarizer(
                build=lambda messages: [batches.BatchNote("生日是三月五号", kind="fact", actor="contact")]
            )
        )

        assert await _recall(other, "生日") == []
        assert await _recall(conversation, "生日") != []

    @pytest.mark.asyncio
    async def test_default_summarizer_calls_fast_model(self, batch_env, monkeypatch):
        """默认总结器(不注入): 走 fast 模型 + JSON 解析,失败不当成崩溃。"""
        from langchain_core.messages import AIMessage

        class FakeModel:
            """只实现 ainvoke 的假模型(替代 ChatOpenAI,测试不联网)。"""

            def __init__(self) -> None:
                self.prompts: list[str] = []

            async def ainvoke(self, messages):
                self.prompts.append("\n".join(str(m.content) for m in messages))
                return AIMessage(
                    content='```json\n[{"content": "小号喜欢喝美式", "kind": "fact", "actor": "contact"}]\n```'
                )

        model = FakeModel()
        monkeypatch.setattr(batches, "_fast_model", lambda: model)

        conversation = _make_conversation("50006", monitor=1, digest_max_messages=2)
        _add_message(conversation["id"], "我喜欢喝美式", message_id="p1")
        _add_message(conversation["id"], "好的", message_id="p2")

        summary = await batches.run_due_batches()  # 不注入 → 用默认实现

        assert summary["written"] == 1
        assert len(model.prompts) == 1
        assert "我喜欢喝美式" in model.prompts[0]      # 消息进了提示词
        hits = await _recall(conversation, "美式")
        assert any("美式" in hit.fact for hit in hits)

    @pytest.mark.asyncio
    async def test_ingest_node_does_not_write_memory(self, batch_env):
        """回归: ingest 只落 messages 表,不再逐条写长期记忆。"""
        from app.agent.nodes import ingest

        conversation = _make_conversation("50004", monitor=1)
        await ingest.run(
            {
                "conversation_id": conversation["id"],
                "event": {
                    "kind": "message",
                    "event_id": "in-1",
                    "text": "我下周三要去北京出差",
                    "is_self": False,
                },
            }
        )

        from app.services import conversations as conversations_service

        assert len(conversations_service.list_messages(conversation["id"])) == 1
        assert await _recall(conversation, "出差") == []

    @pytest.mark.asyncio
    async def test_finalize_node_does_not_write_memory(self, batch_env):
        """回归: finalize 只把回复落库/发送,不再逐条写长期记忆。"""
        from langchain_core.messages import AIMessage

        from app.agent.nodes import finalize

        conversation = _make_conversation("50005", monitor=1)
        sent: list[tuple[str, str, str]] = []

        async def sender(conversation_id: str, text: str, source: str) -> None:
            sent.append((conversation_id, text, source))

        await finalize.run(
            {
                "conversation_id": conversation["id"],
                "messages": [AIMessage(content=json.dumps({"action": "reply", "reply_text": "知道了"}) )],
            },
            sender,
        )

        assert sent == [(conversation["id"], "知道了", "reply")]
        assert await _recall(conversation, "知道了") == []


# ---------------------------------------------------------------------------
# 纯宿主侧逻辑(不依赖 Doppel 也能跑)
# ---------------------------------------------------------------------------
class TestHostSideHelpers:
    def test_cursor_roundtrip(self):
        """游标是 (created_at, id) 复合键:id 为空表示"只比时间"。"""
        encoded = batches.encode_cursor("2026-09-10T12:00:00+00:00", "msg-1")
        assert batches.decode_cursor(encoded) == ("2026-09-10T12:00:00+00:00", "msg-1")
        assert batches.decode_cursor("2026-09-10T12:00:00+00:00|") == (
            "2026-09-10T12:00:00+00:00",
            "",
        )

    def test_parse_notes_tolerates_model_noise(self):
        """模型偶尔用代码块包 JSON 或夹带解释文字 —— 解析要能容错,不能抛。"""
        fenced = '```json\n[{"content": "喜欢美式", "kind": "fact", "actor": "contact"}]\n```'
        notes = batches.parse_notes(fenced)
        assert [note.content for note in notes] == ["喜欢美式"]
        assert notes[0].actor == "contact"

        # "没价值"就该是空数组;纯文字输出按"没总结出东西"处理(不抛异常)
        assert batches.parse_notes("[]") == []
        assert batches.parse_notes("这段聊天没什么可记的") == []
        assert batches.parse_notes("") == []

    def test_actor_sql_matches_memory_actor_rule(self, temp_data_dir):
        """SQL 里的 actor 映射必须与 memory._actor_of 完全一致(否则会读错人)。

        两处映射(一处 Python、一处 SQL)是刻意的重复:分页必须在 SQL 过滤之后
        发生。这个测试就是防止它们漂移。
        """
        from app.db import get_connection, init_db
        from app.services import conversations as conversations_service

        init_db()
        conversation_id = conversations_service.ensure_conversation("qq", "private", "60001")
        combos = [
            ("user", "inbound"),
            ("assistant", "outbound"),
            ("user", "outbound"),
            ("assistant", "inbound"),
            ("system", "system"),
            ("user", "system"),
            ("system", "inbound"),
        ]
        for index, (role, source) in enumerate(combos):
            message_id = _add_message(
                conversation_id, f"m{index}", message_id=f"a{index}", role=role, source=source
            )
            row = get_connection().execute(
                f"SELECT {batches._ACTOR_SQL} AS actor FROM messages WHERE id=?", (message_id,)
            ).fetchone()
            assert row["actor"] == memory_layer._actor_of(role, source), (role, source)

    def test_pending_count_uses_watermark(self, temp_data_dir):
        """待处理条数只算水位线之后的消息。"""
        from app.db import init_db
        from app.services import conversations as conversations_service

        init_db()
        conversation_id = conversations_service.ensure_conversation("qq", "private", "60002")
        early = _now(-600)
        for index in range(3):
            _add_message(conversation_id, f"旧{index}", at=early)

        assert batches.pending_of(conversation_id, "") == (3, early)
        assert batches.pending_of(conversation_id, early) == (0, "")

    def test_task_version_change_resets_cursor(self):
        """总结语义变更(升 TASK_VERSION)时旧游标作废,但水位线保留。

        为什么必须这样: 游标是"按旧规则已经处理到哪"的位置;规则变了,
        这个位置不再可信 —— 而水位线只表示"这些消息写进库了",与规则无关,
        所以退回水位线重读既不会漏,也不会重读到更早的消息。
        """
        stale = {
            "cursor": "2026-09-10T12:00:00+00:00|msg-9",
            "last_message_at": "2026-09-10T12:00:00+00:00",
            "metadata": json.dumps({"task_version": "0"}),
        }
        assert batches._checkpoint_of(stale) == ("", "2026-09-10T12:00:00+00:00")

        fresh = dict(stale, metadata=json.dumps({"task_version": batches.TASK_VERSION}))
        assert batches._checkpoint_of(fresh)[0] == "2026-09-10T12:00:00+00:00|msg-9"


# ---------------------------------------------------------------------------
# 调度挂载
# ---------------------------------------------------------------------------
class TestSchedulerHook:
    @pytest.mark.asyncio
    async def test_scan_is_throttled_and_errors_swallowed(self, batch_env, monkeypatch):
        """攒批扫描必须低频,且异常不能冒泡到调度循环。"""
        from app.scheduler import BATCH_SCAN_INTERVAL_SECONDS, Scheduler

        calls: list[int] = []

        async def fake_run_due_batches(now=None, *, summarizer=None):
            calls.append(1)
            return {"enabled": True, "checked": 0, "runs": [], "written": 0}

        monkeypatch.setattr(batches, "run_due_batches", fake_run_due_batches)
        scheduler = Scheduler()

        # 第一次: 启动后立即补扫一次(上次扫描时刻初始为 0)
        await scheduler._maybe_scan_batches()
        assert scheduler._batch_task is not None
        await scheduler._batch_task
        assert calls == [1]

        # 紧接着的第二次: 间隔没到,跳过(不能每秒扫库)
        await scheduler._maybe_scan_batches()
        assert calls == [1]

        # 模拟间隔已过: 再扫一次
        scheduler._last_batch_scan = 0.0
        await scheduler._maybe_scan_batches()
        assert scheduler._batch_task is not None
        await scheduler._batch_task
        assert calls == [1, 1]

        # 扫描内部异常: 就地吞掉(调度循环照常跑)
        async def boom(now=None, *, summarizer=None):
            raise RuntimeError("扫描炸了")

        monkeypatch.setattr(batches, "run_due_batches", boom)
        scheduler._last_batch_scan = 0.0
        await scheduler._maybe_scan_batches()
        assert scheduler._batch_task is not None
        await scheduler._batch_task  # 不应抛出

        assert BATCH_SCAN_INTERVAL_SECONDS >= 30, "攒批扫描不该是秒级的"

    @pytest.mark.asyncio
    async def test_poll_loop_runs_batch_scan(self, batch_env, monkeypatch):
        """攒批扫描确实挂进了轮询循环(不是只有单测里能跑)。"""
        import asyncio

        from app import scheduler as scheduler_module

        monkeypatch.setattr(scheduler_module, "BATCH_SCAN_INTERVAL_SECONDS", 0.01)
        calls: list[int] = []

        async def fake_run_due_batches(now=None, *, summarizer=None):
            calls.append(1)
            return {"enabled": True, "checked": 0, "runs": [], "written": 0}

        monkeypatch.setattr(batches, "run_due_batches", fake_run_due_batches)

        scheduler = scheduler_module.Scheduler()
        scheduler.start()
        # 轮询首轮立即执行(循环体在 sleep 之前),这里只等它跑够几轮
        await asyncio.sleep(0.3)
        await scheduler.stop()

        assert calls, "轮询循环没有触发攒批扫描"

    @pytest.mark.asyncio
    async def test_stop_cancels_pending_scan(self, batch_env, monkeypatch):
        """退出时要把还没跑完的攒批任务取消掉,不留悬挂任务。"""
        import asyncio

        from app.scheduler import Scheduler

        async def slow(now=None, *, summarizer=None):
            await asyncio.sleep(30)
            return {"enabled": True, "checked": 0, "runs": [], "written": 0}

        monkeypatch.setattr(batches, "run_due_batches", slow)
        scheduler = Scheduler()
        scheduler.start()
        scheduler._last_batch_scan = 0.0
        await scheduler._maybe_scan_batches()
        assert scheduler._batch_task is not None

        await scheduler.stop()
        assert scheduler._batch_task is None
        assert scheduler._task is None
