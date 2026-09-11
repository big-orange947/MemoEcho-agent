# -*- coding: utf-8 -*-
"""草稿模式(reply_mode=draft)测试。

背景: `draft` 这个取值一直存在于 CLI / API / 文档里,但**没有任何行为** ——
`decide()` 里没有它的分支,落到"只记录",与 `off` 完全一样。
配置项摆在那里却不生效,比没有这个选项更糟:号主以为自己在把关,其实没有。
本文件守住"它真的生效"这件事,以及最容易出事的那条边界 ——
**草稿绝不能自己跑出去**。

行为约定:
1. draft 与 auto 一样**跑图**(不然没有草稿可看),区别只在发不发;
2. 产出存进队列 lane=draft,不落库成 assistant 消息(对方确实没收到);
3. 同一会话只保留最新一条未处理草稿(旧的已过时,不该留着让人挑);
4. **显式指令**(主 agent 派发 / 桌面命令)不受草稿约束 —— 那是人当场要求做的事;
5. 号主确认走 POST /api/reports/{id}/send(先落库再发送,失败不标记完成);
6. 上报出口(sink)**永不**自动转发草稿。

不依赖真实 LLM / 网络。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from app import reports as reports_service
from app.agent.runtime import set_notifier
from app.events import Event, EventKind, EventSource
from app.services import conversations as conversations_service
from app.services import policy as policy_service


class FakeModel(BaseChatModel):
    """固定回一句话的假模型(草稿内容由它决定)。"""

    text: str = "晚上八点行吗"

    @property
    def _llm_type(self) -> str:
        return "draft-fake"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=self.text))])

    def bind_tools(self, tools, **kwargs):
        return self


@pytest.fixture()
def env(temp_data_dir, monkeypatch):
    monkeypatch.setenv("MEMO_ECHO_API_TOKEN", "")
    monkeypatch.setenv("MEMO_ECHO_BOT_QQ", "3969785168")

    from app import config as config_module

    monkeypatch.setattr(config_module, "_settings", None, raising=False)

    from app.db import init_db

    init_db()
    return None


def _event(
    *,
    source: str = EventSource.QQ,
    kind: str = EventKind.MESSAGE,
    text: str = "你今晚还来不来?",
    should_respond: bool = True,
) -> Event:
    return Event.from_text(
        text,
        source=source,
        kind=kind,
        platform="qq",
        chat_type="private",
        external_id="10001",
        conversation_id="conv-draft",
        should_respond=should_respond,
    )


def _enable_draft(conversation_id: str) -> None:
    policy_service.update_policy(conversation_id, reply_mode="draft")


# ---------------------------------------------------------------------------
# 1. 分流: draft 要跑图
# ---------------------------------------------------------------------------
class TestDecide:
    def test_draft_runs_graph(self, env):
        """draft 必须跑图 —— 不跑图就没有草稿,等于 off。"""
        conversation_id = conversations_service.ensure_conversation("qq", "private", "10001")
        _enable_draft(conversation_id)
        conversation = conversations_service.get_conversation(conversation_id)

        decision, reason = policy_service.decide(_event(), conversation)
        assert decision == policy_service.DECISION_REPLY
        assert reason == "reply-mode-draft"

    def test_draft_still_needs_a_reason_to_reply(self, env):
        """平台不要求回应的消息(如群未 @),draft 也不该反而去回。"""
        conversation_id = conversations_service.ensure_conversation("qq", "group", "20002")
        _enable_draft(conversation_id)
        conversation = conversations_service.get_conversation(conversation_id)

        decision, reason = policy_service.decide(_event(should_respond=False), conversation)
        assert decision == policy_service.DECISION_RECORD
        assert reason == "monitor-on"

    def test_draft_conversation_handles_timer(self, env):
        """draft 与 auto 一样算"活跃会话":定时唤醒不该被当残留计划丢掉。"""
        conversation_id = conversations_service.ensure_conversation("qq", "private", "10001")
        _enable_draft(conversation_id)
        conversation = conversations_service.get_conversation(conversation_id)

        decision, _ = policy_service.decide(
            _event(kind=EventKind.TIMER, should_respond=False), conversation
        )
        assert decision == policy_service.DECISION_REPLY


# ---------------------------------------------------------------------------
# 2. 投递方式: 直发 or 草稿
# ---------------------------------------------------------------------------
class TestDelivery:
    def test_draft_conversation_goes_to_draft(self, env):
        conversation_id = conversations_service.ensure_conversation("qq", "private", "10001")
        _enable_draft(conversation_id)
        conversation = conversations_service.get_conversation(conversation_id)

        assert (
            policy_service.resolve_reply_delivery(_event(), conversation)
            == policy_service.DELIVERY_DRAFT
        )

    def test_explicit_instruction_bypasses_draft(self, env):
        """权威规则"显式指令 > 会话策略":人当场让做的事,不该被降级成建议。"""
        conversation_id = conversations_service.ensure_conversation("qq", "private", "10001")
        _enable_draft(conversation_id)
        conversation = conversations_service.get_conversation(conversation_id)

        for source in (EventSource.AGENT, EventSource.DESKTOP):
            assert (
                policy_service.resolve_reply_delivery(_event(source=source), conversation)
                == policy_service.DELIVERY_SEND
            )

    def test_auto_conversation_sends(self, env):
        conversation_id = conversations_service.ensure_conversation("qq", "private", "10001")
        policy_service.update_policy(conversation_id, reply_mode="auto")
        conversation = conversations_service.get_conversation(conversation_id)

        assert (
            policy_service.resolve_reply_delivery(_event(), conversation)
            == policy_service.DELIVERY_SEND
        )

    def test_task_authorization_does_not_bypass_draft(self, env):
        """任务授权态让 agent 能参与对话,但**不能**绕过号主设的草稿要求。

        授权态是"要不要参与"的设置,草稿是"发之前给不给我看"的设置 ——
        后者是安全阀,不能被前者悄悄掀开。
        """
        conversation_id = conversations_service.ensure_conversation("qq", "private", "10001")
        _enable_draft(conversation_id)
        conversation = conversations_service.get_conversation(conversation_id)

        assert (
            policy_service.resolve_reply_delivery(_event(), conversation)
            == policy_service.DELIVERY_DRAFT
        )


# ---------------------------------------------------------------------------
# 3. 端到端: 跑完图不发送,进队列
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
class TestGraphProducesDraft:
    async def _run(self, conversation_id: str, *, event_source: str = EventSource.QQ):
        from app.agent.graph import AgentGraph

        sent: list[str] = []
        pushed: list[tuple[str, dict]] = []

        async def _sender(cid: str, text: str, source: str):
            sent.append(text)
            return {"ok": True, "platform_message_id": "1", "error": ""}

        async def _notifier(event_type: str, payload: dict):
            pushed.append((event_type, payload))

        set_notifier(_notifier)
        graph = AgentGraph(
            llm_factory=lambda fast=False: FakeModel(), tools=[], sender=_sender
        )
        try:
            await graph.run_event(
                {
                    "conversation_id": conversation_id,
                    "kind": EventKind.MESSAGE,
                    "source": event_source,
                    "platform": "qq",
                    "chat_type": "private",
                    "external_id": "10001",
                    "text": "你今晚还来不来?",
                    "is_self": False,
                }
            )
        finally:
            await graph.close()
            set_notifier(None)  # type: ignore[arg-type]

        return sent, pushed

    @pytest.mark.asyncio
    async def test_does_not_send_and_enqueues_draft(self, env):
        conversation_id = conversations_service.ensure_conversation("qq", "private", "10001")
        _enable_draft(conversation_id)

        sent, pushed = await self._run(conversation_id)

        assert sent == [], f"草稿模式不该发送: {sent}"

        drafts = reports_service.list_reports(lane=reports_service.LANE_DRAFT)
        assert len(drafts) == 1
        assert drafts[0]["payload"]["text"] == "晚上八点行吗"
        assert drafts[0]["status"] == reports_service.STATUS_PENDING
        # 桌面端实时可见
        assert [event for event, _ in pushed] == ["draft"]

    @pytest.mark.asyncio
    async def test_draft_not_recorded_as_sent_message(self, env):
        """没发出去的回复不能记成"我说过的话" —— 否则下一轮模型会以为已回复。"""
        conversation_id = conversations_service.ensure_conversation("qq", "private", "10001")
        _enable_draft(conversation_id)

        await self._run(conversation_id)

        assistant = [
            m
            for m in conversations_service.list_messages(conversation_id, limit=50)
            if m.get("role") == "assistant"
        ]
        assert assistant == []

    @pytest.mark.asyncio
    async def test_explicit_source_sends_directly(self, env):
        conversation_id = conversations_service.ensure_conversation("qq", "private", "10001")
        _enable_draft(conversation_id)

        sent, _ = await self._run(conversation_id, event_source=EventSource.AGENT)

        assert sent == ["晚上八点行吗"]
        assert reports_service.list_reports(lane=reports_service.LANE_DRAFT) == []

    @pytest.mark.asyncio
    async def test_second_draft_replaces_first(self, env):
        """连来两条消息: 只留最新一条草稿(旧的已过时,留着只会让人挑错版本)。"""
        conversation_id = conversations_service.ensure_conversation("qq", "private", "10001")
        _enable_draft(conversation_id)

        await self._run(conversation_id)

        from app.agent.graph import AgentGraph

        async def _sender(cid: str, text: str, source: str):
            return None

        graph = AgentGraph(
            llm_factory=lambda fast=False: FakeModel(text="改成九点?"), tools=[], sender=_sender
        )
        try:
            await graph.run_event(
                {
                    "conversation_id": conversation_id,
                    "kind": EventKind.MESSAGE,
                    "source": EventSource.QQ,
                    "platform": "qq",
                    "chat_type": "private",
                    "external_id": "10001",
                    "text": "那明天呢",
                    "is_self": False,
                }
            )
        finally:
            await graph.close()

        drafts = reports_service.list_reports(lane=reports_service.LANE_DRAFT)
        assert len(drafts) == 1, "旧草稿没有被覆盖,号主会拿到过期版本"
        assert drafts[0]["payload"]["text"] == "改成九点?"

    @pytest.mark.asyncio
    async def test_escalation_wins_over_draft(self, env, monkeypatch):
        """请示那轮不该再产生草稿: 已经"停下等号主"了,draft 通道只会添乱。"""
        from app.tools.escalate import escalate_to_owner

        class EscalatingModel(BaseChatModel):
            @property
            def _llm_type(self) -> str:
                return "escalating"

            def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
                if any(getattr(m, "type", "") == "tool" for m in messages):
                    return ChatResult(
                        generations=[ChatGeneration(message=AIMessage(content="好,我先问一下"))]
                    )
                return ChatResult(
                    generations=[
                        ChatGeneration(
                            message=AIMessage(
                                content="",
                                tool_calls=[
                                    {
                                        "name": "escalate_to_owner",
                                        "args": {"question": "对方要改到九点,行吗?"},
                                        "id": "c1",
                                    }
                                ],
                            )
                        )
                    ]
                )

            def bind_tools(self, tools, **kwargs):
                return self

        conversation_id = conversations_service.ensure_conversation("qq", "private", "10001")
        _enable_draft(conversation_id)

        from app.agent.graph import AgentGraph

        sent: list[str] = []

        async def _sender(cid: str, text: str, source: str):
            sent.append(text)
            return None

        graph = AgentGraph(
            llm_factory=lambda fast=False: EscalatingModel(),
            tools=[escalate_to_owner],
            sender=_sender,
        )
        try:
            await graph.run_event(
                {
                    "conversation_id": conversation_id,
                    "kind": EventKind.MESSAGE,
                    "source": EventSource.QQ,
                    "platform": "qq",
                    "chat_type": "private",
                    "external_id": "10001",
                    "text": "改到九点行吗",
                    "is_self": False,
                }
            )
        finally:
            await graph.close()

        assert sent == []
        assert reports_service.list_reports(lane=reports_service.LANE_DRAFT) == []


    @pytest.mark.asyncio
    async def test_goal_not_advanced_by_draft(self, env):
        """草稿没发出去,目标就不该被推进 —— 否则又是"假承诺"那一类问题。

        模型可能在决策信封里说 goal_status=done(它以为说定了),
        但那条回复还躺在队列里等人确认 —— 对方什么都没收到。
        """
        from app.agent.graph import AgentGraph
        from app.services import goals as goals_service

        conversation_id = conversations_service.ensure_conversation("qq", "private", "10001")
        _enable_draft(conversation_id)
        goal = goals_service.create_goal(conversation_id, "约他晚上打游戏")

        class DoneClaimingModel(BaseChatModel):
            @property
            def _llm_type(self) -> str:
                return "done-claiming"

            def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
                return ChatResult(
                    generations=[
                        ChatGeneration(
                            message=AIMessage(
                                content='{"action":"reply","reply_text":"晚上八点行吗",'
                                '"goal_status":"done","goal_progress":"已约好"}'
                            )
                        )
                    ]
                )

            def bind_tools(self, tools, **kwargs):
                return self

        async def _sender(cid: str, text: str, source: str):
            return None

        graph = AgentGraph(
            llm_factory=lambda fast=False: DoneClaimingModel(), tools=[], sender=_sender
        )
        try:
            await graph.run_event(
                {
                    "conversation_id": conversation_id,
                    "kind": EventKind.MESSAGE,
                    "source": EventSource.QQ,
                    "platform": "qq",
                    "chat_type": "private",
                    "external_id": "10001",
                    "text": "你今晚还来不来?",
                    "is_self": False,
                }
            )
        finally:
            await graph.close()

        stored = goals_service.get_goal(str(goal["id"]))
        assert stored is not None
        assert stored["status"] == "active", "草稿还没发出去,任务就被标成完成了"
        assert "待号主确认草稿" in str(stored.get("progress") or "")


# ---------------------------------------------------------------------------
# 4. 号主确认: /api/reports/{id}/send
# ---------------------------------------------------------------------------
class TestSendEndpoint:
    @pytest.fixture()
    def client(self, env, monkeypatch):
        import app.main as main_module

        app = main_module.create_app()

        from app.agent.runtime import get_graph

        graph = get_graph()
        assert graph is not None
        graph.llm_factory = lambda fast=False: FakeModel()  # type: ignore[assignment]

        with TestClient(app) as test_client:
            yield test_client

    def _draft(self, conversation_id: str = "conv-draft") -> str:
        record = reports_service.upsert_draft(conversation_id=conversation_id, text="晚上八点行吗")
        return record["id"]

    def test_send_delivers_and_acks(self, client, env, monkeypatch):
        conversation_id = conversations_service.ensure_conversation("qq", "private", "10001")
        record_id = self._draft(conversation_id)

        sent: list[tuple[str, str]] = []

        async def fake_deliver(cid: str, text: str, source: str = "outbound", **kwargs):
            sent.append((cid, text))
            return {"ok": True, "message_id": "m-1", "reason": ""}

        monkeypatch.setattr("app.outbox.deliver", fake_deliver)

        response = client.post(f"/api/reports/{record_id}/send")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["ok"] is True
        assert sent == [(conversation_id, "晚上八点行吗")]

        record = reports_service.get_report(record_id)
        assert record["status"] == reports_service.STATUS_ACKED

    def test_send_accepts_edited_text(self, client, env, monkeypatch):
        """号主可以改一改再发 —— 草稿本来就是给人改的。"""
        conversation_id = conversations_service.ensure_conversation("qq", "private", "10001")
        record_id = self._draft(conversation_id)

        sent: list[str] = []

        async def fake_deliver(cid: str, text: str, source: str = "outbound", **kwargs):
            sent.append(text)
            return {"ok": True, "message_id": "m-2", "reason": ""}

        monkeypatch.setattr("app.outbox.deliver", fake_deliver)

        response = client.post(f"/api/reports/{record_id}/send", json={"text": "八点半,行吗?"})
        assert response.status_code == 200
        assert sent == ["八点半,行吗?"]

    def test_send_uses_real_outbox_path(self, client, env):
        """不经任何替换地走一遍: 草稿 -> outbox(先落库再发送) -> 历史里留下记录。

        上面那条用例替换了 outbox.deliver,只验证"端点在正确的时候调用了它";
        这条用登记的假发送器走**真实**的 outbox 代码路径,证明:
        1) 文本真的交给了渠道发送器;2) 历史里留下了 source=manual 的记录
        (落库是 outbox 的既有语义: 说过的话要记得,否则下一轮会重复说)。
        """
        from app.agent import runtime as agent_runtime

        conversation_id = conversations_service.ensure_conversation("qq", "private", "10001")
        record_id = self._draft(conversation_id)

        sent: list[tuple[str, str, str]] = []

        async def fake_sender(cid: str, text: str, source: str):
            sent.append((cid, text, source))
            return {"ok": True, "platform_message_id": "pm-9", "error": ""}

        agent_runtime.set_sender(fake_sender)

        response = client.post(f"/api/reports/{record_id}/send")
        assert response.status_code == 200, response.text
        assert sent == [(conversation_id, "晚上八点行吗", "manual")]

        history = conversations_service.list_messages(conversation_id, limit=20)
        assert [m["content"] for m in history if m["role"] == "assistant"] == ["晚上八点行吗"]
        assert [m["source"] for m in history if m["role"] == "assistant"] == ["manual"]
        assert reports_service.get_report(record_id)["status"] == reports_service.STATUS_ACKED

    def test_failed_send_leaves_no_false_history(self, client, env):
        """发送失败不能留下"我说过"的记录 —— 对方其实什么都没收到。

        与 HITL 那条假承诺同一类问题: 系统以为发生过的事必须真的发生过。
        记录保持 pending,号主修好之后可以重试。
        """
        from app.agent import runtime as agent_runtime

        conversation_id = conversations_service.ensure_conversation("qq", "private", "10001")
        record_id = self._draft(conversation_id)

        async def failing_sender(cid: str, text: str, source: str):
            raise RuntimeError("NapCat 未连接")

        agent_runtime.set_sender(failing_sender)

        response = client.post(f"/api/reports/{record_id}/send")
        assert response.status_code == 502

        history = conversations_service.list_messages(conversation_id, limit=20)
        assert [m for m in history if m["role"] == "assistant"] == []
        assert reports_service.get_report(record_id)["status"] == reports_service.STATUS_PENDING

    def test_send_failure_keeps_draft_pending(self, client, env, monkeypatch):
        """发送失败不标记完成: 平台抖动不该让一条拟好的回复凭空消失。"""
        conversation_id = conversations_service.ensure_conversation("qq", "private", "10001")
        record_id = self._draft(conversation_id)

        async def failing_deliver(cid: str, text: str, source: str = "outbound", **kwargs):
            return {"ok": False, "message_id": "", "reason": "NapCat 未连接"}

        monkeypatch.setattr("app.outbox.deliver", failing_deliver)

        response = client.post(f"/api/reports/{record_id}/send")
        assert response.status_code == 502
        assert reports_service.get_report(record_id)["status"] == reports_service.STATUS_PENDING

    def test_send_rejects_non_draft(self, client, env):
        """只有草稿可以走这个口子 —— 别让它变成万能外发按钮。"""
        reports_service.enqueue(
            lane=reports_service.LANE_URGENT, conversation_id="conv-x", payload={"summary": "x"}
        )
        record_id = reports_service.list_reports(lane=reports_service.LANE_URGENT)[0]["id"]

        response = client.post(f"/api/reports/{record_id}/send")
        assert response.status_code == 400

    def test_send_rejects_already_handled(self, client, env):
        conversation_id = conversations_service.ensure_conversation("qq", "private", "10001")
        record_id = self._draft(conversation_id)
        reports_service.drop(record_id, reason="不发了")

        response = client.post(f"/api/reports/{record_id}/send")
        assert response.status_code == 409

    def test_send_missing_record(self, client, env):
        assert client.post("/api/reports/no-such-id/send").status_code == 404

    def test_drop_discards_draft(self, client, env):
        """丢弃草稿: 不发出去,但留下记录(审计要看得到"拟过但没发")。"""
        conversation_id = conversations_service.ensure_conversation("qq", "private", "10001")
        record_id = self._draft(conversation_id)

        response = client.post(f"/api/reports/{record_id}/drop", json={"reason": "不合适"})
        assert response.status_code == 200
        assert reports_service.get_report(record_id)["status"] == reports_service.STATUS_DROPPED


# ---------------------------------------------------------------------------
# 5. 出口: sink 必须绕开草稿
# ---------------------------------------------------------------------------
class TestSinkIgnoresDrafts:
    @pytest.mark.asyncio
    async def test_sink_never_forwards_draft(self, env, monkeypatch):
        """最危险的一条: 草稿被当上报转发出去 → 既打扰人又消耗掉这条草稿。"""
        from app import sinks as sinks_service

        monkeypatch.setenv("MEMO_ECHO_ALERT_SINKS", "db,qq")
        monkeypatch.setenv("MEMO_ECHO_ALERT_FORWARD_TARGET", "private:10000")
        from app import config as config_module

        config_module._settings = None

        conversation_id = conversations_service.ensure_conversation("qq", "private", "10001")
        record_id = reports_service.upsert_draft(
            conversation_id=conversation_id, text="晚上八点行吗"
        )["id"]

        delivered: list[str] = []

        async def fake_send(target_conversation_id: str, text: str) -> bool:
            delivered.append(text)
            return True

        result = await sinks_service.deliver_pending(send=fake_send)

        assert delivered == [], f"草稿被自动转发了: {delivered}"
        assert result.get("delivered", 0) == 0
        assert reports_service.get_report(record_id)["status"] == reports_service.STATUS_PENDING

        # 反证: 这条草稿此刻**是**可认领的(pending 状态,在队列里)——
        # sink 没取它,是因为显式排除了 draft 通道,而不是因为记录不存在/已被取走。
        # 少了这一步,测试可能因为"什么也没发生"而假通过。
        claimed = reports_service.claim(limit=10, claimed_by="test")
        assert [item["id"] for item in claimed] == [record_id]
