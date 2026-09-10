# -*- coding: utf-8 -*-
"""会话配置 API 测试(PATCH / resolve / 会话视图)。

这是"人(或上游主 agent)怎么配置值守"的对外入口,必须稳:
- 按三元组建会话(默认全关的会话没有消息,只能这样找到它);
- 改策略(含蕴含关系与非法值拒绝);
- 改人设/注意事项;
- 返回归一化视图(布尔与列表,而不是库里的 0/1 与 JSON 字符串)。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(temp_data_dir, monkeypatch):
    monkeypatch.setenv("MEMO_ECHO_WEBHOOK_ASYNC", "false")
    monkeypatch.setenv("MEMO_ECHO_API_TOKEN", "")

    from app import config as config_module

    monkeypatch.setattr(config_module, "_settings", None, raising=False)

    import app.main as main_module

    app = main_module.create_app()
    with TestClient(app) as test_client:
        yield test_client


class TestResolveConversation:
    def test_resolve_creates_conversation(self, client):
        """按三元组建会话 —— 默认全关的会话没有任何消息,这是唯一入口。"""
        resp = client.post(
            "/api/conversations/resolve",
            json={"platform": "qq", "chat_type": "private", "external_id": "2597164807", "title": "小号"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"]
        assert body["title"] == "小号"
        # 新会话默认全关
        assert body["policy"]["monitor"] is False
        assert body["policy"]["reply_mode"] == "off"

    def test_resolve_is_idempotent(self, client):
        first = client.post(
            "/api/conversations/resolve",
            json={"platform": "qq", "chat_type": "private", "external_id": "10001"},
        ).json()
        second = client.post(
            "/api/conversations/resolve",
            json={"platform": "qq", "chat_type": "private", "external_id": "10001"},
        ).json()
        assert first["id"] == second["id"]

    def test_resolve_requires_identity(self, client):
        assert client.post("/api/conversations/resolve", json={"platform": "qq"}).status_code == 400


class TestUpdateConversation:
    def test_configure_reply(self, client):
        """主 agent 的"开始自动回复与 XXX"落到这里。"""
        conv = client.post(
            "/api/conversations/resolve",
            json={"platform": "qq", "chat_type": "private", "external_id": "2597164807"},
        ).json()

        resp = client.patch(
            f"/api/conversations/{conv['id']}",
            json={"reply_mode": "auto", "persona": "别答应晚上十点后的活动"},
        )
        assert resp.status_code == 200
        body = resp.json()

        assert body["conversation"]["policy"]["reply_mode"] == "auto"
        # 开回复会自动开监视,并且明确回报出来
        assert body["conversation"]["policy"]["monitor"] is True
        assert "monitor" in body["implied"]
        assert body["conversation"]["persona"] == "别答应晚上十点后的活动"

    def test_monitor_only(self, client):
        """只监视不回复: 这是"静默记录"的配置形态。"""
        conv = client.post(
            "/api/conversations/resolve",
            json={"platform": "qq", "chat_type": "group", "external_id": "55555"},
        ).json()
        body = client.patch(f"/api/conversations/{conv['id']}", json={"monitor": True}).json()
        policy = body["conversation"]["policy"]
        assert policy["monitor"] is True
        assert policy["reply_mode"] == "off"

    def test_invalid_reply_mode_rejected(self, client):
        conv = client.post(
            "/api/conversations/resolve",
            json={"platform": "qq", "chat_type": "private", "external_id": "10001"},
        ).json()
        resp = client.patch(f"/api/conversations/{conv['id']}", json={"reply_mode": "always"})
        assert resp.status_code == 400

    def test_unknown_field_rejected(self, client):
        conv = client.post(
            "/api/conversations/resolve",
            json={"platform": "qq", "chat_type": "private", "external_id": "10001"},
        ).json()
        resp = client.patch(f"/api/conversations/{conv['id']}", json={"nonsense": 1})
        assert resp.status_code == 400

    def test_missing_conversation_404(self, client):
        assert client.patch("/api/conversations/no-such", json={"monitor": True}).status_code == 404

    def test_get_conversation_view(self, client):
        conv = client.post(
            "/api/conversations/resolve",
            json={"platform": "qq", "chat_type": "private", "external_id": "10001"},
        ).json()
        got = client.get(f"/api/conversations/{conv['id']}").json()
        assert got["id"] == conv["id"]
        assert isinstance(got["policy"]["alert_keywords"], list)
        assert got["policy"]["digest_window_seconds"] == 1800

    def test_list_includes_policy(self, client):
        client.post(
            "/api/conversations/resolve",
            json={"platform": "qq", "chat_type": "private", "external_id": "10001"},
        )
        items = client.get("/api/conversations").json()
        assert items and "policy" in items[0]
        assert items[0]["policy"]["reply_mode"] == "off"
