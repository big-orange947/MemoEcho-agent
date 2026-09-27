# =============================================================================
# tests/test_console_api.py - 控制台(对话线程 + 自然语言执行入口)
# -----------------------------------------------------------------------------
# 说明: 与 tests/test_api.py / test_ui_api.py 同款约定 —— TestClient + 假 LLM,
# 不联网、不依赖真实模型。
#
# 这里最关键的一组用例是"执行轨迹": 控制台的价值就是"看得见 agent 干了什么",
# 所以工具调用/结果必须真的落进 agent_steps,而不是只体现在最终回复里。
# =============================================================================

from __future__ import annotations

import time
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field


class ScriptedChatModel(BaseChatModel):
    """按脚本依次返回消息的假模型(用来构造"先调工具、再汇报"的 ReAct 循环)。

    脚本里的每一项: 字符串 ⇒ 普通回复; 列表 ⇒ 一次工具调用(名字 + 参数)。
    """

    script: list[Any] = Field(default_factory=list)
    counter: int = 0

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        index = min(self.counter, len(self.script) - 1) if self.script else 0
        self.counter += 1
        step = self.script[index] if self.script else "好"

        if isinstance(step, str):
            message = AIMessage(content=step)
        else:
            name, args = step
            message = AIMessage(
                content="",
                tool_calls=[{"name": name, "args": args, "id": f"call_{self.counter}", "type": "tool_call"}],
            )
        return ChatResult(generations=[ChatGeneration(message=message)])

    def bind_tools(self, tools, **kwargs):
        return self


def _build_app(monkeypatch, script: list[Any]):
    monkeypatch.setenv("MEMO_ECHO_WEBHOOK_ASYNC", "false")
    monkeypatch.setenv("MEMO_ECHO_API_TOKEN", "")

    from app import config as config_module

    monkeypatch.setattr(config_module, "_settings", None, raising=False)

    import app.main as main_module

    app = main_module.create_app()

    from app.agent.runtime import get_graph

    graph = get_graph()
    assert graph is not None
    # 模型实例跨轮共用: 图每轮 reason 都会调 llm_factory,若每次新建实例,
    # 脚本计数器会归零 —— 表现为永远返回脚本第一条(无限调同一工具)。
    model = ScriptedChatModel(script=script)
    graph.llm_factory = lambda fast=False: model  # type: ignore[assignment]
    return app


@pytest.fixture()
def make_client(temp_data_dir, monkeypatch):
    """返回"按脚本建客户端"的工厂(每个用例自己决定模型怎么回)。

    注意两点:
      · 手动 __enter__ 才能跑 lifespan(后台任务需要事件循环);
      · 结束时必须 __exit__ —— 否则 TestClient 的 loop/线程会留到整个 session,
        后面用例的 teardown 会撞上"task attached to a different loop"。
    """

    created: list[TestClient] = []

    def build(script: list[Any]):
        app = _build_app(monkeypatch, script)
        client = TestClient(app)
        client.__enter__()
        created.append(client)
        return client

    yield build

    for client in created:
        client.__exit__(None, None, None)


def _wait_run(client: TestClient, run_id: str, timeout: float = 10.0) -> dict[str, Any]:
    """轮询到执行结束(后台任务是异步的,HTTP 返回时还没跑完)。"""
    deadline = time.time() + timeout
    run: dict[str, Any] = {}
    while time.time() < deadline:
        run = client.get(f"/api/runs/{run_id}").json()
        if run.get("status") != "running":
            return run
        time.sleep(0.05)
    raise AssertionError(f"执行未在 {timeout}s 内结束: {run}")


# ---------------------------------------------------------------------------
# 线程 CRUD
# ---------------------------------------------------------------------------
class TestThreads:
    def test_create_and_list(self, make_client):
        client = make_client(["好的"])
        created = client.post("/api/threads", json={"title": "组会安排"}).json()
        assert created["id"]
        assert created["title"] == "组会安排"
        assert created["archived"] is False
        assert created["running"] == 0

        threads = client.get("/api/threads").json()
        assert [t["id"] for t in threads] == [created["id"]]

    def test_rename_and_archive(self, make_client):
        client = make_client(["好的"])
        thread_id = client.post("/api/threads", json={}).json()["id"]

        renamed = client.patch(f"/api/threads/{thread_id}", json={"title": "改个名"}).json()
        assert renamed["title"] == "改个名"

        # 归档 = 收进抽屉: 默认列表看不到,显式要才给
        archived = client.patch(f"/api/threads/{thread_id}", json={"archived": True}).json()
        assert archived["archived"] is True
        assert client.get("/api/threads").json() == []
        assert len(client.get("/api/threads", params={"include_archived": True}).json()) == 1

        # 取消归档能回到列表
        client.patch(f"/api/threads/{thread_id}", json={"archived": False})
        assert len(client.get("/api/threads").json()) == 1

    def test_delete_removes_thread(self, make_client):
        client = make_client(["好的"])
        thread_id = client.post("/api/threads", json={}).json()["id"]
        assert client.delete(f"/api/threads/{thread_id}").json()["deleted"] == thread_id
        assert client.get("/api/threads").json() == []

    def test_unknown_thread_is_404(self, make_client):
        client = make_client(["好的"])
        assert client.get("/api/threads/nope/runs").status_code == 404
        assert client.post("/api/threads/nope/messages", json={"text": "在吗"}).status_code == 404
        assert client.patch("/api/threads/nope", json={"title": "x"}).status_code == 404

    def test_empty_text_rejected(self, make_client):
        client = make_client(["好的"])
        thread_id = client.post("/api/threads", json={}).json()["id"]
        assert client.post(f"/api/threads/{thread_id}/messages", json={"text": "   "}).status_code == 400

    def test_qq_conversation_is_not_a_thread(self, make_client):
        """QQ 会话不能被当线程操作 —— 两类东西语义不同,混用会误删值守会话。"""
        client = make_client(["好的"])
        conversation_id = client.post(
            "/api/conversations/resolve",
            json={"platform": "qq", "chat_type": "private", "external_id": "2597164807"},
        ).json()["id"]
        assert client.get(f"/api/threads/{conversation_id}/runs").status_code == 404


# ---------------------------------------------------------------------------
# 自然语言入口 + 执行轨迹
# ---------------------------------------------------------------------------
class TestConsoleRun:
    def test_instruction_runs_and_records_tool_steps(self, make_client, monkeypatch):
        """核心用例: 一条指令 ⇒ 自己找人、自己发消息、过程留痕、结果汇报。"""
        client = make_client(
            [
                ("list_contacts", {}),
                ("send_qq_message", {"chat_id": "2597164807", "text": "今晚有空打游戏吗"}),
                "已经问过 km 了,等他回。",
            ]
        )

        # 假发送器: 工具层的成功路径(真发要靠 NapCat,这里只验证链路与埋点)。
        # 必须在建 app **之后**打补丁 —— create_app 里会 init_sender 覆盖掉。
        sent: list[tuple] = []

        async def fake_sender(platform, chat_type, external_id, text, origin):
            sent.append((platform, chat_type, external_id, text, origin))
            return True

        from app.tools import messaging

        monkeypatch.setattr(messaging, "_sender", fake_sender)

        thread_id = client.post("/api/threads", json={"title": "约游戏"}).json()["id"]

        resp = client.post(f"/api/threads/{thread_id}/messages", json={"text": "帮我问一下 km 今晚有没有空打游戏"})
        assert resp.status_code == 202
        payload = resp.json()
        assert payload["run_id"] and payload["thread_id"] == thread_id

        run = _wait_run(client, payload["run_id"])
        assert run["status"] == "done", run
        assert "km" in run["reply"]
        assert run["instruction"] == "帮我问一下 km 今晚有没有空打游戏"

        # 轨迹: 两次工具调用,各有"发起"与"返回"两条,顺序正确
        kinds = [(s["kind"], s["name"]) for s in run["steps"]]
        assert kinds == [
            ("tool_call", "list_contacts"),
            ("tool_result", "list_contacts"),
            ("tool_call", "send_qq_message"),
            ("tool_result", "send_qq_message"),
        ]
        assert all(s["ok"] for s in run["steps"])
        # 参数与结果都要能看见(这是"看得见 agent 干了什么"的实质)
        call = next(s for s in run["steps"] if s["kind"] == "tool_call" and s["name"] == "send_qq_message")
        assert "2597164807" in call["detail"] and "今晚有空打游戏吗" in call["detail"]
        result = next(s for s in run["steps"] if s["kind"] == "tool_result" and s["name"] == "send_qq_message")
        assert "已发送" in result["detail"]

        # 真的发出去了(经工具层,带来源会话)
        assert sent and sent[0][2] == "2597164807" and sent[0][4] == thread_id

        # 用户指令与 agent 汇报都进了线程消息流
        messages = client.get(f"/api/threads/{thread_id}/messages").json()
        assert [m["role"] for m in messages] == ["user", "assistant"]
        assert messages[0]["content"] == "帮我问一下 km 今晚有没有空打游戏"

        # 目标: 指令即目标,agent 自己判断完成
        goals = client.get(f"/api/conversations/{thread_id}/goals").json()
        assert len(goals) == 1
        assert goals[0]["objective"] == "帮我问一下 km 今晚有没有空打游戏"

    def test_failed_tool_call_is_marked(self, make_client):
        """工具失败要留痕且标红 —— 不然后端失败在前端看起来像成功。"""
        client = make_client(
            [
                ("send_qq_message", {"chat_id": "999", "text": "在吗"}),
                "发失败了,机器人好像没在线。",
            ]
        )
        thread_id = client.post("/api/threads", json={}).json()["id"]
        run_id = client.post(f"/api/threads/{thread_id}/messages", json={"text": "给 999 发个消息"}).json()["run_id"]

        run = _wait_run(client, run_id)
        assert run["status"] == "done"
        result = next(s for s in run["steps"] if s["kind"] == "tool_result")
        assert result["ok"] is False
        assert "失败" in result["detail"]

    def test_reply_only_run_has_no_tool_steps(self, make_client):
        """纯聊天: 没有工具调用就不该有工具步骤(轨迹要如实)。"""
        client = make_client(["你好,有什么要我办的?"])
        thread_id = client.post("/api/threads", json={}).json()["id"]
        run_id = client.post(f"/api/threads/{thread_id}/messages", json={"text": "你好"}).json()["run_id"]

        run = _wait_run(client, run_id)
        assert run["status"] == "done"
        assert run["reply"] == "你好,有什么要我办的?"
        assert run["steps"] == []

    def test_graph_failure_becomes_error_run(self, make_client, monkeypatch):
        """图抛异常 ⇒ run 标 error 且带原因,不能静默停在 running。"""
        from app.agent import runtime as runtime_module

        class BrokenGraph:
            def is_busy(self, conversation_id: str) -> bool:
                return False

            async def run_event(self, payload):
                raise RuntimeError("模型服务不可用")

        client = make_client(["好的"])
        thread_id = client.post("/api/threads", json={}).json()["id"]

        # 换掉图: 后台任务是在 create_task 之后才取 get_graph() 的,
        # 所以必须**一直换到执行结束** —— 只换到 POST 返回会随机跑成真图。
        original = runtime_module.get_graph()
        monkeypatch.setattr(runtime_module, "_graph", BrokenGraph())
        try:
            run_id = client.post(f"/api/threads/{thread_id}/messages", json={"text": "在吗"}).json()["run_id"]
            run = _wait_run(client, run_id)
        finally:
            monkeypatch.setattr(runtime_module, "_graph", original)

        assert run["status"] == "error"
        assert "模型服务不可用" in run["error"]
        assert any(s["kind"] == "error" for s in run["steps"])

    def test_runs_list_is_ordered(self, make_client):
        client = make_client(["第一条回复", "第二条回复"])
        thread_id = client.post("/api/threads", json={}).json()["id"]
        first = client.post(f"/api/threads/{thread_id}/messages", json={"text": "一"}).json()["run_id"]
        _wait_run(client, first)
        second = client.post(f"/api/threads/{thread_id}/messages", json={"text": "二"}).json()["run_id"]
        _wait_run(client, second)

        runs = client.get(f"/api/threads/{thread_id}/runs").json()
        assert [r["instruction"] for r in runs] == ["一", "二"]
        assert runs[0]["reply"] == "第一条回复"

    def test_second_instruction_while_running_is_429(self, make_client, monkeypatch):
        """同一条线程串行执行: 上一条还在跑时,再发一条要明确拒绝而不是排队乱跑。"""
        from app.agent import runtime as runtime_module

        class AlwaysBusyGraph:
            def is_busy(self, conversation_id: str) -> bool:
                return True

            async def run_event(self, payload):  # pragma: no cover - 不该被调用
                return ""

        client = make_client(["好的"])
        thread_id = client.post("/api/threads", json={}).json()["id"]

        original = runtime_module.get_graph()
        monkeypatch.setattr(runtime_module, "_graph", AlwaysBusyGraph())
        try:
            resp = client.post(f"/api/threads/{thread_id}/messages", json={"text": "再办一件事"})
        finally:
            monkeypatch.setattr(runtime_module, "_graph", original)
        assert resp.status_code == 429

    def test_console_uses_owner_facing_prompt(self, make_client):
        """控制台线程要用"办事员"提示词: 对面是号主,不是联系人。

        这是控制台能不能用的分水岭 —— 用替身提示词,agent 会对着号主演联系人,
        既不汇报也不认错。所以这里直接断言提示词选择的结果。
        """
        from app.agent.nodes.reason import _build_messages

        state = {
            "working_memory": {"platform": "desktop", "chat_type": "thread", "hitl": True},
            "messages": [],
        }
        console_system = _build_messages(state, "- send_qq_message: 发消息")[0].content
        assert "号主自己" in console_system
        assert "不暴露你是 AI" not in console_system.split("底线")[0].replace("对外发言时不要暴露自己是 AI", "")

        # 值守会话仍是替身提示词(不能把控制台那套带过去)
        state["working_memory"] = {"platform": "qq", "chat_type": "private", "hitl": True}
        qq_system = _build_messages(state, "- send_qq_message: 发消息")[0].content
        assert "正在一个会话中与人聊天" in qq_system
        assert "号主自己" not in qq_system
