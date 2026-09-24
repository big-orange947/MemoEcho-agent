"""前端控制台接口测试(app/api/ui.py)。

覆盖:
- GET /api/tools: 清单结构、high_risk 标记、default_for 与策略层同源;
- GET /api/configs + PUT /api/configs/{key}: 白名单(允许的能写、其余 400);
- GET /api/goals: 跨会话列表、status 过滤、limit 上界、按 updated_at 倒序;
- 前端静态产物: 目录不存在时不挂载(也不报错),存在时挂载且不抢 /api/*。

说明: 照 tests/test_api.py 的约定 —— TestClient + 假 LLM,不联网、不依赖真模型。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field


class FakeChatModel(BaseChatModel):
    """固定回复的假模型(只需要"不联网",本文件的用例基本不跑图)。

    注意: 计数器放在实例字段里 —— pydantic 模型不允许通过类属性累加
    (会抛 AttributeError,见 tests/test_api.py)。
    """

    responses: list[str] = Field(default_factory=lambda: ["收到啦"])
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


def _build_app(monkeypatch, web_dist: Path | None = None):
    """组装一个测试用 app(带假 LLM),返回 (app, main_module)。

    web_dist: 传了就把 WEB_DIST_DIR 换成它(**必须在 create_app 之前**替换,
    因为"要不要挂载前端"是在组装时判断的)。
    """
    monkeypatch.setenv("MEMO_ECHO_WEBHOOK_ASYNC", "false")
    monkeypatch.setenv("MEMO_ECHO_API_TOKEN", "")

    from app import config as config_module

    monkeypatch.setattr(config_module, "_settings", None, raising=False)

    import app.main as main_module

    if web_dist is not None:
        monkeypatch.setattr(main_module, "WEB_DIST_DIR", web_dist)

    app = main_module.create_app()

    # 图在组装时就持有 llm_factory,必须在跑图之前换成假的
    from app.agent.runtime import get_graph

    graph = get_graph()
    assert graph is not None
    graph.llm_factory = lambda fast=False: FakeChatModel()  # type: ignore[assignment]
    return app, main_module


@pytest.fixture()
def client(temp_data_dir, monkeypatch):
    """带假 LLM 的测试客户端(数据目录为临时目录)。"""
    app, _ = _build_app(monkeypatch)
    with TestClient(app) as test_client:
        yield test_client


def _seed_goals() -> dict[str, str]:
    """造两个会话 + 两个目标(一个 active、一个 done),返回各 ID。

    直接用服务层建数据: 本文件测的是读取接口,不关心目标是怎么被 agent 推进的。
    """
    from app.services import conversations as conversations_service
    from app.services import goals as goals_service

    conv_private = conversations_service.ensure_conversation("qq", "private", "10001")
    conv_group = conversations_service.ensure_conversation("qq", "group", "20002")
    active = goals_service.create_goal(conv_private, "问 km 今晚几点上课")
    done = goals_service.create_goal(conv_group, "订周五的场地")
    # 标记完成会刷新 updated_at ⇒ 它排在列表最前
    goals_service.update_goal_status(done["id"], "done", "已订好")
    return {
        "private": conv_private,
        "group": conv_group,
        "active_id": active["id"],
        "done_id": done["id"],
    }


# ---------------------------------------------------------------------------
# 工具清单
# ---------------------------------------------------------------------------
class TestToolsApi:
    def test_tools_shape_and_high_risk(self, client):
        """工具清单: 高危标记 + 私聊/群聊默认授权(前端授权面板直接照着渲染)。"""
        tools = client.get("/api/tools").json()
        assert tools, "图已初始化,工具清单不该为空"

        by_name = {t["name"]: t for t in tools}
        assert "send_qq_message" in by_name

        send = by_name["send_qq_message"]
        assert send["high_risk"] is True
        assert "high_risk" in send["tags"]
        # 私聊默认给,群聊默认不给(群聊人数多,替号主发声风险最高)
        assert send["default_for"] == {"private": True, "group": False}
        assert send["description"].strip(), "工具说明不能为空(前端要展示给人看)"

        # 非高危工具: 两种会话都默认给
        safe = by_name["resolve_contact"]
        assert safe["high_risk"] is False
        assert safe["default_for"] == {"private": True, "group": True}

    def test_default_for_uses_policy_rule(self, client):
        """default_for 必须与策略层同源(否则前端显示与实际放行会不一致)。"""
        from app.agent.runtime import get_graph
        from app.services import policy as policy_service

        graph = get_graph()
        registry = {t.name: list(getattr(t, "tags", None) or []) for t in graph.tools}
        expected_group = policy_service.resolve_allowed_tools({"chat_type": "group"}, registry)
        expected_private = policy_service.resolve_allowed_tools({"chat_type": "private"}, registry)

        for item in client.get("/api/tools").json():
            assert item["default_for"]["group"] == (item["name"] in expected_group)
            assert item["default_for"]["private"] == (item["name"] in expected_private)

    def test_tools_empty_when_graph_missing(self, client, monkeypatch):
        """图未初始化时返回空数组,而不是 500(只读接口不该因组装顺序而失败)。"""
        from app.api import ui as ui_api

        monkeypatch.setattr(ui_api, "get_graph", lambda: None)
        resp = client.get("/api/tools")
        assert resp.status_code == 200
        assert resp.json() == []


# ---------------------------------------------------------------------------
# 全局配置(白名单写入)
# ---------------------------------------------------------------------------
class TestConfigsApi:
    def test_get_configs_empty(self, client):
        body = client.get("/api/configs").json()
        assert body["configs"] == {}

    def test_put_alert_contacts(self, client):
        resp = client.put("/api/configs/alert_contacts", json={"value": "2597164807"})
        assert resp.status_code == 200
        assert resp.json() == {"key": "alert_contacts", "value": "2597164807"}
        # 真的落库了
        assert client.get("/api/configs").json()["configs"]["alert_contacts"] == "2597164807"

    def test_put_contact_aliases_structured_value(self, client):
        """联系人别名是 JSON 对象: 前端直接传 dict 也能写(统一序列化)。"""
        resp = client.put(
            "/api/configs/contact_aliases",
            json={"value": {"小号": {"chat_type": "private", "external_id": "2597164807"}}},
        )
        assert resp.status_code == 200
        stored = client.get("/api/configs").json()["configs"]["contact_aliases"]
        assert json.loads(stored)["小号"]["external_id"] == "2597164807"

    def test_put_non_whitelisted_key_rejected(self, client):
        """非白名单键(如内部用量计数)必须 400,且**真的没有写进去**。"""
        key = "alert_llm_usage:2026-09-24"
        resp = client.put(f"/api/configs/{key}", json={"value": "0"})
        assert resp.status_code == 400
        assert key in resp.json()["detail"]
        assert key not in client.get("/api/configs").json()["configs"]

    def test_put_requires_value(self, client):
        assert client.put("/api/configs/alert_contacts", json={}).status_code == 400
        assert client.put("/api/configs/alert_contacts", json={"value": None}).status_code == 400


# ---------------------------------------------------------------------------
# 跨会话目标
# ---------------------------------------------------------------------------
class TestGoalsApi:
    def test_list_all_goals(self, client):
        ids = _seed_goals()
        goals = client.get("/api/goals").json()
        assert len(goals) == 2
        # 契约字段齐全(前端按这些字段渲染进度卡)
        assert set(goals[0]) == {
            "id",
            "conversation_id",
            "objective",
            "status",
            "progress",
            "created_at",
            "updated_at",
        }
        assert {g["id"] for g in goals} == {ids["active_id"], ids["done_id"]}

    def test_ordering_by_updated_at_desc(self, client):
        ids = _seed_goals()
        goals = client.get("/api/goals").json()
        # 刚被标记完成的那条 updated_at 最新 ⇒ 排在最前
        assert goals[0]["id"] == ids["done_id"]
        assert goals[0]["status"] == "done"
        assert goals[0]["progress"] == "已订好"

    def test_status_filter(self, client):
        ids = _seed_goals()
        active = client.get("/api/goals", params={"status": "active"}).json()
        assert [g["id"] for g in active] == [ids["active_id"]]

        done = client.get("/api/goals", params={"status": "done"}).json()
        assert [g["id"] for g in done] == [ids["done_id"]]

        # 空 = 全部
        assert len(client.get("/api/goals", params={"status": ""}).json()) == 2

    def test_invalid_status_rejected(self, client):
        """拼错的过滤器要报错,而不是静默返回全部(否则前端渲染会静默错位)。"""
        resp = client.get("/api/goals", params={"status": "running"})
        assert resp.status_code == 400

    def test_limit_bounds(self, client):
        _seed_goals()
        assert len(client.get("/api/goals", params={"limit": 1}).json()) == 1
        # 上界 200、下界 1: 越界交给 FastAPI 校验(422),不在业务层悄悄夹取
        assert client.get("/api/goals", params={"limit": 0}).status_code == 422
        assert client.get("/api/goals", params={"limit": 201}).status_code == 422


# ---------------------------------------------------------------------------
# 前端静态产物托管
# ---------------------------------------------------------------------------
# main.create_app 里给静态挂载起的名字(Starlette 会把 mount 的 path 归一化成 "",
# 按 path 找不到它,所以测试按名字定位)
_MOUNT_NAME = "web"


class TestStaticMount:
    def test_not_mounted_when_dist_missing(self, temp_data_dir, monkeypatch):
        """web/dist 不存在 ⇒ 不挂载、不报错(纯后端部署/CI 的常态)。"""
        app, _ = _build_app(monkeypatch, web_dist=Path(temp_data_dir) / "web" / "dist")
        assert not [r for r in app.routes if getattr(r, "name", None) == _MOUNT_NAME]

        # 没有静态兜底时,接口照常可用
        with TestClient(app) as test_client:
            assert test_client.get("/api/conversations").status_code == 200

    def test_mounted_and_api_still_wins(self, temp_data_dir, monkeypatch):
        """web/dist 存在 ⇒ 挂在 "/" 上;但 /api/* 与 /qq/webhook 仍优先命中。"""
        dist = Path(temp_data_dir) / "web" / "dist"
        dist.mkdir(parents=True)
        (dist / "index.html").write_text("<html>memo-echo-web</html>", encoding="utf-8")

        app, _ = _build_app(monkeypatch, web_dist=dist)

        # 注意: Starlette 会把 mount 的 path 归一化成 ""(不是 "/"),
        # 所以按名字找,别按 path 匹配。
        mounts = [r for r in app.routes if getattr(r, "name", None) == _MOUNT_NAME]
        assert len(mounts) == 1, "静态目录存在却没挂载"

        with TestClient(app) as test_client:
            home = test_client.get("/")
            assert home.status_code == 200
            assert "memo-echo-web" in home.text
            # 关键: 静态兜底不能吃掉显式路由
            assert test_client.get("/api/conversations").status_code == 200
            assert test_client.get("/api/configs").status_code == 200
            assert test_client.post("/qq/webhook", json={"post_type": "meta_event"}).status_code == 200
