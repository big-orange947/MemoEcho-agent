# -*- coding: utf-8 -*-
"""外部调度入口测试(app/api/dispatch.py)。

覆盖:
- 四种 kind 的行为(send_message 直接发 / note 只记录 / handle_message 与 task 走 agent);
- 幂等键(重复投递不重复执行、不重复发送);
- deadline 过期拒绝;
- 目标解析(conversation_id 与三元组两种写法);
- 参数校验与鉴权;
- 任务状态与产物查询。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult


class FakeChatModel(BaseChatModel):
    """固定回复的假模型(不联网)。"""

    responses: list[str] = ["好的"]
    counter: int = 0

    @property
    def _llm_type(self) -> str:
        return "fake"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self.counter += 1
        text = self.responses[min(self.counter - 1, len(self.responses) - 1)]
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=text))])

    def bind_tools(self, tools, **kwargs):
        return self


@pytest.fixture()
def client(temp_data_dir, monkeypatch):
    """带 fake LLM 的测试客户端(webhook 同步模式,便于断言)。"""
    monkeypatch.setenv("MEMO_ECHO_WEBHOOK_ASYNC", "false")
    monkeypatch.setenv("MEMO_ECHO_API_TOKEN", "")
    monkeypatch.setenv("MEMO_ECHO_BOT_QQ", "3969785168")

    from app import config as config_module

    monkeypatch.setattr(config_module, "_settings", None, raising=False)

    import app.main as main_module

    app = main_module.create_app()

    from app.agent.runtime import get_graph

    graph = get_graph()
    assert graph is not None
    graph.llm_factory = lambda fast=False: FakeChatModel(responses=["好的"])

    with TestClient(app) as test_client:
        yield test_client


# ---------------------------------------------------------------------------
# 参数校验
# ---------------------------------------------------------------------------
class TestValidation:
    def test_invalid_kind(self, client):
        resp = client.post("/api/dispatch", json={"kind": "nonsense"})
        assert resp.status_code == 400
        assert "kind" in resp.json()["detail"]

    def test_missing_target(self, client):
        resp = client.post(
            "/api/dispatch",
            json={"kind": "send_message", "text": "hi"},
        )
        assert resp.status_code == 400
        assert "target" in resp.json()["detail"]

    def test_send_message_requires_content(self, client):
        resp = client.post(
            "/api/dispatch",
            json={"kind": "send_message", "target": {"conversation_id": "d-1"}},
        )
        assert resp.status_code == 400
        assert "content" in resp.json()["detail"]

    def test_task_requires_instruction(self, client):
        resp = client.post(
            "/api/dispatch",
            json={"kind": "task", "target": {"conversation_id": "d-1"}},
        )
        assert resp.status_code == 400
        assert "instruction" in resp.json()["detail"]

    def test_token_required_when_configured(self, client, monkeypatch):
        """配置了 api_token 后,无 token 的请求应被拒绝。"""
        from app import config as config_module

        monkeypatch.setattr(config_module, "_settings", None, raising=False)
        monkeypatch.setenv("MEMO_ECHO_API_TOKEN", "secret-token")

        resp = client.post(
            "/api/dispatch",
            json={
                "kind": "note",
                "text": "x",
                "target": {"conversation_id": "d-auth"},
            },
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# send_message: 直接发送(不经 agent)
# ---------------------------------------------------------------------------
class TestSendMessage:
    def test_direct_send_records_and_marks_done(self, client):
        resp = client.post(
            "/api/dispatch",
            json={
                "caller": "main-agent",
                "kind": "send_message",
                "target": {"conversation_id": "conv-dispatch-send"},
                "text": "帮我把这句话发出去",
            },
        )
        assert resp.status_code == 202
        task = resp.json()
        assert task["status"] in ("done", "accepted")

        # 查任务: 应完成并带 message_id
        detail = client.get(f"/api/dispatch/{task['task_id']}").json()
        assert detail["status"] == "done"
        assert detail["result"]["message_id"]
        assert detail["result"]["sent_text"] == "帮我把这句话发出去"

        # 消息应落库(出站记录)
        msgs = client.get("/api/conversations/conv-dispatch-send/messages").json()
        outbound = [m for m in msgs if m["source"] == "dispatch"]
        assert len(outbound) == 1
        assert outbound[0]["content"] == "帮我把这句话发出去"
        # 直接发送不应触发 agent(没有 assistant 的"回复"产生)
        assert all(m["source"] != "reply" for m in msgs)


# ---------------------------------------------------------------------------
# note: 仅记录
# ---------------------------------------------------------------------------
class TestNote:
    def test_note_only_audits(self, client):
        resp = client.post(
            "/api/dispatch",
            json={
                "caller": "main-agent",
                "kind": "note",
                "target": {"conversation_id": "conv-dispatch-note"},
                "text": "背景信息: 他今天出差",
            },
        )
        assert resp.status_code == 202
        task = resp.json()
        detail = client.get(f"/api/dispatch/{task['task_id']}").json()
        assert detail["status"] == "done"

        # 审计有记录,但没有对话消息产生
        events = client.get("/api/events", params={"conversation_id": "conv-dispatch-note"}).json()
        assert any("出差" in (e["summary"] or "") for e in events)
        msgs = client.get("/api/conversations/conv-dispatch-note/messages").json()
        assert msgs == []


# ---------------------------------------------------------------------------
# handle_message / task: 走 agent
# ---------------------------------------------------------------------------
class TestAgentKinds:
    def test_handle_message_runs_agent(self, client):
        resp = client.post(
            "/api/dispatch",
            json={
                "caller": "outer-system",
                "kind": "handle_message",
                "target": {"conversation_id": "conv-dispatch-handle"},
                "content": [{"type": "text", "text": "外部转来的消息"}],
                "result_mode": "poll",
            },
        )
        assert resp.status_code == 202
        task = resp.json()

        detail = client.get(f"/api/dispatch/{task['task_id']}").json()
        # 同步模式下处理已完成
        assert detail["status"] == "done"
        assert detail["result"]["reply"] == "好的"

        msgs = client.get("/api/conversations/conv-dispatch-handle/messages").json()
        assert [m["role"] for m in msgs] == ["user", "assistant"]
        assert msgs[0]["content"] == "外部转来的消息"

    def test_task_creates_goal(self, client):
        resp = client.post(
            "/api/dispatch",
            json={
                "caller": "main-agent",
                "kind": "task",
                "target": {"conversation_id": "conv-dispatch-task"},
                "instruction": "问 km 今晚几点上课,然后转告小号",
            },
        )
        assert resp.status_code == 202
        task = resp.json()
        assert client.get(f"/api/dispatch/{task['task_id']}").json()["status"] == "done"

        # 指令应变成会话上的目标
        goals = client.get("/api/conversations/conv-dispatch-task/goals").json()
        assert len(goals) == 1
        assert "今晚几点上课" in goals[0]["objective"]


# ---------------------------------------------------------------------------
# configure: 主 agent 用自然语言配置会话值守
# ---------------------------------------------------------------------------
class TestConfigure:
    def test_configure_reply_and_note(self, client):
        """典型场景: "开始自动回复与 XXX 的会话,注意事项是别提钱"。"""
        resp = client.post(
            "/api/dispatch",
            json={
                "caller": "main-agent",
                "kind": "configure",
                "target": {"platform": "qq", "chat_type": "private", "external_id": "2597164807"},
                "policy": {"reply_mode": "auto", "note": "别提钱"},
            },
        )
        assert resp.status_code == 202
        task = client.get(f"/api/dispatch/{resp.json()['task_id']}").json()
        assert task["status"] == "done"

        result = task["result"]
        assert result["policy"]["reply_mode"] == "auto"
        assert result["policy"]["monitor"] is True      # 开回复自动蕴含监视
        assert result["persona"] == "别提钱"

        # 配置要真的落到会话上(不只是回报一个结果)
        conv = client.post(
            "/api/conversations/resolve",
            json={"platform": "qq", "chat_type": "private", "external_id": "2597164807"},
        ).json()
        assert conv["policy"]["reply_mode"] == "auto"
        assert conv["persona"] == "别提钱"

    def test_configure_monitor_only(self, client):
        """只监视: 静默收集消息,不回复。"""
        resp = client.post(
            "/api/dispatch",
            json={
                "caller": "main-agent",
                "kind": "configure",
                "target": {"conversation_id": "conv-cfg-monitor"},
                "policy": {"monitor": True},
            },
        )
        assert resp.status_code == 202
        task = client.get(f"/api/dispatch/{resp.json()['task_id']}").json()
        assert task["result"]["policy"]["monitor"] is True
        assert task["result"]["policy"]["reply_mode"] == "off"

    def test_configure_requires_policy(self, client):
        resp = client.post(
            "/api/dispatch",
            json={"kind": "configure", "target": {"conversation_id": "conv-x"}},
        )
        assert resp.status_code == 400

    def test_configure_rejects_unknown_field(self, client):
        resp = client.post(
            "/api/dispatch",
            json={
                "kind": "configure",
                "target": {"conversation_id": "conv-x"},
                "policy": {"whatever": 1},
            },
        )
        assert resp.status_code == 400

    def test_configure_sends_no_message(self, client):
        """配置动作绝不能顺手对外发消息 —— 这是它和 send_message 的根本区别。"""
        client.post(
            "/api/dispatch",
            json={
                "kind": "configure",
                "target": {"platform": "qq", "chat_type": "private", "external_id": "88888"},
                "policy": {"monitor": True},
            },
        )
        convs = client.get("/api/conversations").json()
        target = next(c for c in convs if c["external_id"] == "88888")
        assert client.get(f"/api/conversations/{target['id']}/messages").json() == []

    def test_configure_is_idempotent(self, client):
        """重复投递同样的配置请求不会重复执行(幂等键)。"""
        payload = {
            "caller": "main-agent",
            "kind": "configure",
            "target": {"conversation_id": "conv-cfg-idem"},
            "policy": {"reply_mode": "auto"},
            "idempotency_key": "cfg-1",
        }
        first = client.post("/api/dispatch", json=payload).json()
        second = client.post("/api/dispatch", json=payload).json()
        assert first["task_id"] == second["task_id"]
        assert second.get("duplicated") is True


# ---------------------------------------------------------------------------
# 幂等
# ---------------------------------------------------------------------------
class TestIdempotency:
    def test_duplicate_key_does_not_resend(self, client):
        """同一幂等键重复投递: 只发送一次(关键护栏: 防重试导致重复发消息)。"""
        payload = {
            "caller": "main-agent",
            "kind": "send_message",
            "target": {"conversation_id": "conv-dispatch-idem"},
            "text": "只应发送一次",
            "idempotency_key": "task-abc-123",
        }
        first = client.post("/api/dispatch", json=payload).json()
        second = client.post("/api/dispatch", json=payload).json()

        # 两次返回同一个任务
        assert first["task_id"] == second["task_id"]
        assert second.get("duplicated") is True

        # 消息只发了一条
        msgs = client.get("/api/conversations/conv-dispatch-idem/messages").json()
        assert len(msgs) == 1

    def test_no_key_creates_separate_tasks(self, client):
        payload = {
            "kind": "send_message",
            "target": {"conversation_id": "conv-dispatch-nokey"},
            "text": "没有幂等键",
        }
        first = client.post("/api/dispatch", json=payload).json()
        second = client.post("/api/dispatch", json=payload).json()
        assert first["task_id"] != second["task_id"]


# ---------------------------------------------------------------------------
# deadline
# ---------------------------------------------------------------------------
class TestDeadline:
    def test_expired_deadline_rejected(self, client):
        resp = client.post(
            "/api/dispatch",
            json={
                "kind": "send_message",
                "target": {"conversation_id": "conv-dispatch-expired"},
                "text": "迟到的话",
                "deadline": "2020-01-01T00:00:00+00:00",   # 早已过期
            },
        )
        assert resp.status_code == 408

    def test_future_deadline_accepted(self, client):
        resp = client.post(
            "/api/dispatch",
            json={
                "kind": "send_message",
                "target": {"conversation_id": "conv-dispatch-future"},
                "text": "来得及",
                "deadline": "2099-01-01T00:00:00+00:00",
            },
        )
        assert resp.status_code == 202


# ---------------------------------------------------------------------------
# 目标解析
# ---------------------------------------------------------------------------
class TestTargetResolution:
    def test_target_by_triple_creates_conversation(self, client):
        """用 platform/chat_type/external_id 指定目标 —— 会话不存在时自动建。"""
        resp = client.post(
            "/api/dispatch",
            json={
                "kind": "send_message",
                "target": {"platform": "qq", "chat_type": "private", "external_id": "88888"},
                "text": "按三元组发送",
            },
        )
        assert resp.status_code == 202
        task = resp.json()
        assert task["conversation_id"]  # 已解析出内部会话 ID

        msgs = client.get(f"/api/conversations/{task['conversation_id']}/messages").json()
        assert msgs[0]["content"] == "按三元组发送"


# ---------------------------------------------------------------------------
# 列表查询
# ---------------------------------------------------------------------------
class TestListing:
    def test_list_and_filter(self, client):
        client.post(
            "/api/dispatch",
            json={
                "caller": "caller-a",
                "kind": "send_message",
                "target": {"conversation_id": "conv-list-1"},
                "text": "x",
            },
        )
        client.post(
            "/api/dispatch",
            json={
                "caller": "caller-b",
                "kind": "send_message",
                "target": {"conversation_id": "conv-list-2"},
                "text": "y",
            },
        )

        all_tasks = client.get("/api/dispatch").json()
        assert len(all_tasks) >= 2

        filtered = client.get("/api/dispatch", params={"caller": "caller-a"}).json()
        assert len(filtered) == 1
        assert filtered[0]["caller"] == "caller-a"

    def test_get_unknown_task_404(self, client):
        assert client.get("/api/dispatch/does-not-exist").status_code == 404
