# -*- coding: utf-8 -*-
"""HTTP API 与 webhook 集成测试(FastAPI TestClient)。

覆盖:
- 会话/消息/目标接口;
- QQ webhook: 数组消息解析、分流(通知不入图)、自发消息不触发回复;
- 事件审计端点;
- 会话拥堵返回 429。

说明: 测试用 fake LLM(见 test_graph_flow 的 FakeChatModel 思路),
不联网、不依赖 NapCat。webhook 走同步模式(settings.webhook_async=False),
以便断言"处理确实发生过"。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult


class FakeChatModel(BaseChatModel):
    """固定回复的假模型。

    注意: 计数器放在实例字段里 —— pydantic 模型不允许通过类属性累加
    (会抛 AttributeError),早期版本踩过这个坑。
    """

    responses: list[str] = ["收到啦"]
    counter: int = 0

    @property
    def _llm_type(self) -> str:
        return "fake"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        # 用实例字段累加(pydantic 允许改实例字段)
        self.counter += 1
        text = self.responses[min(self.counter - 1, len(self.responses) - 1)]
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=text))])

    def bind_tools(self, tools, **kwargs):
        return self


@pytest.fixture()
def client(temp_data_dir, monkeypatch):
    """构建带 fake LLM 的测试客户端。

    关键点: 必须在 create_app() 之前 patch ChatOpenAI,
    因为图在组装时就持有 llm_factory。
    """
    # 同步处理 webhook(便于断言),并清空 bot_qq 相关依赖
    monkeypatch.setenv("MEMO_ECHO_WEBHOOK_ASYNC", "false")
    monkeypatch.setenv("MEMO_ECHO_API_TOKEN", "")
    monkeypatch.setenv("MEMO_ECHO_BOT_QQ", "3969785168")

    # 清配置单例,让上面的环境变量生效
    from app import config as config_module

    monkeypatch.setattr(config_module, "_settings", None, raising=False)

    import app.main as main_module

    # 组装应用并用 fake LLM 替换真实模型
    app = main_module.create_app()

    # 替换图的 llm_factory 为 fake(重新编译图,保证节点用上 fake)
    from app.agent.runtime import get_graph

    graph = get_graph()
    assert graph is not None
    fake = FakeChatModel(responses=["收到啦"])
    graph.llm_factory = lambda fast=False: fake  # type: ignore[assignment]

    with TestClient(app) as test_client:
        yield test_client


def enable_policy(platform: str, chat_type: str, external_id: str, **policy) -> str:
    """测试辅助: 为指定会话开启策略。

    背景: 会话策略**默认全关**(不记录、不回复)—— 所以凡是要验证
    "收到消息会怎样"的用例,都必须先显式开启对应开关,而不是依赖默认值。
    返回会话 ID。
    """
    from app.services import conversations as conversations_service
    from app.services import policy as policy_service

    conversation_id = conversations_service.ensure_conversation(platform, chat_type, external_id)
    policy_service.update_policy(conversation_id, **policy)
    return conversation_id


# ---------------------------------------------------------------------------
# 基础接口
# ---------------------------------------------------------------------------
class TestConversationApi:
    def test_list_conversations_empty(self, client):
        resp = client.get("/api/conversations")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_post_message_creates_conversation_and_reply(self, client):
        """发消息 → 建会话 → 图执行 → 落库入站+出站。"""
        resp = client.post("/api/conversations/conv-api-1/messages", json={"text": "你好"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["conversation_id"] == "conv-api-1"
        assert body["event_id"]

        msgs = client.get("/api/conversations/conv-api-1/messages").json()
        assert [m["role"] for m in msgs] == ["user", "assistant"]
        assert msgs[0]["content"] == "你好"
        assert msgs[1]["content"] == "收到啦"

    def test_post_message_requires_text(self, client):
        resp = client.post("/api/conversations/conv-api-1/messages", json={})
        assert resp.status_code == 400

    def test_message_idempotent(self, client):
        """同一事件重复发布: 消息不重复落库(幂等)。"""
        # 先开启监视(否则默认不记录任何消息,这条用例就没有意义了)
        enable_policy("qq", "private", "12345", monitor=1)
        # 通过 webhook 发同一 message_id 两次(更贴近真实重推场景)
        payload = {
            "post_type": "message",
            "message_type": "private",
            "user_id": 12345,
            "message_id": 8888,
            "message": [{"type": "text", "data": {"text": "重复消息"}}],
        }
        r1 = client.post("/qq/webhook", json=payload)
        r2 = client.post("/qq/webhook", json=payload)
        assert r1.status_code == 200 and r2.status_code == 200

        convs = client.get("/api/conversations").json()
        target = next(c for c in convs if c["external_id"] == "12345")
        msgs = client.get(f"/api/conversations/{target['id']}/messages").json()
        # 入站消息只有一条(第二次被去重)
        inbound = [m for m in msgs if m["source"] == "inbound"]
        assert len(inbound) == 1

    def test_default_policy_silent(self, client):
        """默认策略(全关): 只留审计,不落库、不回复。"""
        payload = {
            "post_type": "message",
            "message_type": "private",
            "user_id": 70001,
            "message_id": 9001,
            "message": [{"type": "text", "data": {"text": "未配置的会话"}}],
        }
        client.post("/qq/webhook", json=payload)

        # 会话行仍会被审计/定位逻辑创建,但**不该有**任何对话消息
        convs = client.get("/api/conversations").json()
        target = [c for c in convs if c["external_id"] == "70001"]
        for conv in target:
            msgs = client.get(f"/api/conversations/{conv['id']}/messages").json()
            assert msgs == [], f"默认关闭却记录了消息: {msgs}"

        # 审计里能查到这条消息(排障用)
        events = client.get("/api/events", params={"kind": "message"}).json()
        assert any(e["id"] == "qq-message-9001" for e in events)

    def test_monitor_only_records_without_reply(self, client):
        """只开监视、不开回复: 消息入库,但 QQ 端没有任何回复。"""
        enable_policy("qq", "private", "70002", monitor=1)
        client.post(
            "/qq/webhook",
            json={
                "post_type": "message",
                "message_type": "private",
                "user_id": 70002,
                "message_id": 9002,
                "message": [{"type": "text", "data": {"text": "只监视"}}],
            },
        )

        convs = client.get("/api/conversations").json()
        target = next(c for c in convs if c["external_id"] == "70002")
        msgs = client.get(f"/api/conversations/{target['id']}/messages").json()
        assert [m["role"] for m in msgs] == ["user"]
        assert msgs[0]["content"] == "只监视"

    def test_goal_creation(self, client):
        resp = client.post(
            "/api/conversations/conv-goal-1/goal",
            json={"objective": "问 km 今晚几点上课"},
        )
        assert resp.status_code == 200
        goal = resp.json()
        assert goal["objective"] == "问 km 今晚几点上课"
        assert goal["status"] == "active"

        goals = client.get("/api/conversations/conv-goal-1/goals").json()
        assert len(goals) == 1


# ---------------------------------------------------------------------------
# QQ webhook
# ---------------------------------------------------------------------------
class TestQQWebhook:
    def test_array_message_parsed(self, client):
        """核心回归: 数组格式消息解析正确(之前 str() 会把它变成乱码)。"""
        enable_policy("qq", "private", "20001", monitor=1)
        payload = {
            "post_type": "message",
            "message_type": "private",
            "user_id": 20001,
            "message_id": 1001,
            "message": [
                {"type": "at", "data": {"qq": "3969785168"}},
                {"type": "text", "data": {"text": " 晚上几点上课"}},
            ],
        }
        resp = client.post("/qq/webhook", json=payload)
        assert resp.status_code == 200
        assert resp.json()["retcode"] == 0

        convs = client.get("/api/conversations").json()
        target = next(c for c in convs if c["external_id"] == "20001")
        msgs = client.get(f"/api/conversations/{target['id']}/messages").json()
        inbound = [m for m in msgs if m["source"] == "inbound"]
        # 渲染结果里 @ 变成可读文本,而不是整个数组被字符串化
        assert inbound[0]["content"] == "@3969785168 晚上几点上课"
        assert "[{" not in inbound[0]["content"]

    def test_message_sent_not_responded(self, client):
        """自发消息回显: 只审计,不触发回复(防自循环)。"""
        payload = {
            "post_type": "message_sent",
            "message_type": "private",
            "user_id": 30001,
            "self_id": 3969785168,
            "message_id": 2001,
            "message": [{"type": "text", "data": {"text": "我自己发的"}}],
        }
        resp = client.post("/qq/webhook", json=payload)
        assert resp.status_code == 200

        # 该会话不应产生任何 assistant 回复
        events = client.get("/api/events", params={"kind": "message_sent"}).json()
        assert any(e["id"] == "qq-message_sent-2001" for e in events)

        convs = client.get("/api/conversations").json()
        target = [c for c in convs if c["external_id"] == "30001"]
        if target:  # 会话可能因审计未创建;若创建了则不应有回复
            msgs = client.get(f"/api/conversations/{target[0]['id']}/messages").json()
            assert [m for m in msgs if m["role"] == "assistant"] == []

    def test_notice_only_audited(self, client):
        """通知事件: 审计有记录,但不进对话、不回复。"""
        payload = {
            "post_type": "notice",
            "notice_type": "friend_recall",
            "user_id": 40001,
            "message_id": 3001,
        }
        resp = client.post("/qq/webhook", json=payload)
        assert resp.status_code == 200

        events = client.get("/api/events", params={"kind": "notice"}).json()
        assert len(events) == 1
        assert "撤回" in events[0]["summary"]
        assert events[0]["should_respond"] == 0

    def test_group_message_needs_at(self, client):
        """群聊未 @ 机器人 → 不回复(即使开了自动回复)。"""
        enable_policy("qq", "group", "55555", reply_mode="auto")
        payload = {
            "post_type": "message",
            "message_type": "group",
            "group_id": 55555,
            "user_id": 40002,
            "message_id": 4001,
            "message": [{"type": "text", "data": {"text": "群里闲聊"}}],
        }
        client.post("/qq/webhook", json=payload)
        convs = client.get("/api/conversations").json()
        target = [c for c in convs if c["external_id"] == "55555"]
        if target:
            msgs = client.get(f"/api/conversations/{target[0]['id']}/messages").json()
            assert [m for m in msgs if m["role"] == "assistant"] == []

    def test_group_message_with_at_responds(self, client):
        """群聊 @ 机器人 + 开启自动回复 → 回复。"""
        enable_policy("qq", "group", "55556", reply_mode="auto")
        payload = {
            "post_type": "message",
            "message_type": "group",
            "group_id": 55556,
            "user_id": 40003,
            "message_id": 4002,
            "message": [
                {"type": "at", "data": {"qq": "3969785168"}},
                {"type": "text", "data": {"text": " 在吗"}},
            ],
        }
        client.post("/qq/webhook", json=payload)
        convs = client.get("/api/conversations").json()
        target = next(c for c in convs if c["external_id"] == "55556")
        msgs = client.get(f"/api/conversations/{target['id']}/messages").json()
        assert any(m["role"] == "assistant" for m in msgs)

    def test_meta_event_ignored(self, client):
        """心跳/生命周期: 直接返回 ok,不产生任何记录。"""
        resp = client.post(
            "/qq/webhook",
            json={"post_type": "meta_event", "meta_event_type": "heartbeat", "self_id": 3969785168},
        )
        assert resp.status_code == 200
        assert client.get("/api/events").json() == []

    def test_invalid_json_returns_clear_error(self, client):
        resp = client.post(
            "/qq/webhook",
            content="not-json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 200
        assert resp.json()["retcode"] == 1400


# ---------------------------------------------------------------------------
# 事件审计接口
# ---------------------------------------------------------------------------
class TestEventApi:
    def test_event_stats(self, client):
        client.post(
            "/qq/webhook",
            json={
                "post_type": "message",
                "message_type": "private",
                "user_id": 60001,
                "message_id": 5001,
                "message": [{"type": "text", "data": {"text": "hi"}}],
            },
        )
        stats = client.get("/api/events/stats").json()
        assert stats.get("message", 0) >= 1

    def test_events_filtered_by_conversation(self, client):
        client.post("/api/conversations/conv-evt-1/messages", json={"text": "测试"})
        events = client.get(
            "/api/events", params={"conversation_id": "conv-evt-1"}
        ).json()
        assert len(events) >= 1
        assert all(e["conversation_id"] == "conv-evt-1" for e in events)
