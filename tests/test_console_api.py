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

    脚本里的每一项: 字符串 ⇒ 普通回复; 元组 ⇒ 一次工具调用(名字 + 参数)。
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


class FakeBridge:
    """假 QQ 桥: 让 list_contacts / resolve_contact 在不连 NapCat 时也能返回数据。"""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    async def get_contacts(self) -> dict[str, Any]:
        return self.payload


FAKE_ROSTER: dict[str, Any] = {
    "ok": True,
    "error": "",
    "bot": {"user_id": "3969785168", "nickname": "Memo Echo"},
    "friends": [
        {"user_id": 2597164807, "nickname": "km", "remark": ""},
        {"user_id": 10001, "nickname": "某人", "remark": "小号"},
    ],
    "groups": [{"group_id": 983214567, "group_name": "计科三班", "member_count": 42}],
}


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

        # 假 QQ 桥 + 假发送器(真发要靠 NapCat)。
        # 必须在建 app **之后**打补丁 —— create_app 里会登记真桥、init_sender 覆盖发送器。
        from app.agent import runtime as runtime_module
        from app.tools import messaging

        monkeypatch.setattr(runtime_module, "_bridge", FakeBridge(FAKE_ROSTER))

        sent: list[tuple] = []

        async def fake_sender(platform, chat_type, external_id, text, origin):
            sent.append((platform, chat_type, external_id, text, origin))
            return True

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

    def test_failed_tool_call_is_marked(self, make_client, monkeypatch):
        """工具失败要留痕且标红 —— 不然后端失败在前端看起来像成功。

        失败用**显式注入的失败发送器**造, 不再依赖"测试环境恰好没有 NapCat"——
        测试隔离之后(见 conftest 的 qq_sends), 桥永远返回成功, 那种依赖已不成立。
        """
        client = make_client(
            [
                ("send_qq_message", {"chat_id": "999", "text": "在吗"}),
                "发失败了,机器人好像没在线。",
            ]
        )

        # 必须建完 app 再打补丁: create_app 里会 init_sender 覆盖掉
        from app.tools import messaging

        async def failing_sender(*args):
            return False

        monkeypatch.setattr(messaging, "_sender", failing_sender)
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


# ---------------------------------------------------------------------------
# 跨会话任务: 外联会话里的回复与"进展回流"
# ---------------------------------------------------------------------------
def _link_contact(client: TestClient, contact_qq: str = "3807050597") -> tuple[str, str]:
    """跑一次"派活 → 联系某人",返回 (thread_id, contact_conversation_id)。

    刻意**不**替换 messaging._sender: "建出对方会话 + 把会话登记到目标上"
    都发生在 main.contact_sender 里,替换掉它就绕过了被测逻辑。
    发送本身会失败(测试环境没有 NapCat),不影响这两件事发生。
    """
    from app.services import conversations as conversations_service
    from app.services import goals as goals_service

    thread_id = client.post("/api/threads", json={"title": "问 km"}).json()["id"]
    run_id = client.post(
        f"/api/threads/{thread_id}/messages",
        json={"text": "帮我问一下 km 今晚有没有空打游戏"},
    ).json()["run_id"]
    _wait_run(client, run_id)

    # 目标应已建立,且"联系过的那个人"被登记进目标关联(对方的回复才能唤醒任务)
    goal = goals_service.get_active_goal(thread_id)
    assert goal is not None, "派活应当建立目标"
    contact = conversations_service.find_conversation("qq", "private", contact_qq)
    assert contact is not None, "发消息前应已建出对方的会话"
    assert contact["id"] in goals_service.list_goal_conversations(goal["id"]), (
        "联系过的会话必须登记到目标上 —— 否则对方回话无法唤醒任务"
    )
    return thread_id, contact["id"]


class TestLinkedGoalConversation:
    """真机事故回归: 号主在控制台派活, agent 去联系 km; km 回话后 ——

      · 它把**给号主的汇报**("已经帮你问过 km 了, 等他回")原样发给了 km;
      · 控制台那边什么都没显示(进展没有回流), 号主以为"没反应"。
    这组用例把两件事都钉住。
    """

    def test_milestone_is_reported_back_to_console(self, make_client):
        """对方回话 → 控制台线程出现一条进展(号主才看得见发生了什么)。"""
        client = make_client(
            [
                ("send_qq_message", {"chat_id": "3807050597", "text": "今晚有空打游戏吗"}),
                "已经问过了,等他回。",
                "好嘞,那说定了。",
            ]
        )
        thread_id, _ = _link_contact(client)

        # 对方回话(走真实入口: NapCat 事件上报)
        resp = client.post(
            "/qq/webhook",
            json={
                "post_type": "message",
                "message_type": "private",
                "user_id": 3807050597,
                "message_id": 60001,
                "sender": {"nickname": "㎞"},
                "message": [{"type": "text", "data": {"text": "有的"}}],
            },
        )
        assert resp.status_code == 200

        messages = client.get(f"/api/threads/{thread_id}/messages").json()
        progress = [m for m in messages if m["source"] == "system"]
        assert progress, "对方回话后, 控制台线程应当收到一条进展"
        note = progress[-1]["content"]
        assert "有的" in note and "㎞" in note
        assert note.startswith("【进展】")

    async def test_contact_facing_turn_does_not_leak_owner_report(self, temp_data_dir):
        """给联系人说话时, 系统提示必须是"当事人"口径, 不能是号主视角的目标原文。

        这条钉的是真机事故的根因: 模型拿到了"当前目标: 帮我问一下 km…"这种
        号主视角的句子, 又面对着 km, 于是把汇报说了出去。现在改成:
        本会话是任务外联方 ⇒ 提示词明确"别念给对方、别转述"。
        """
        from app.agent.nodes import retrieve as retrieve_node
        from app.agent.nodes.reason import _build_messages
        from app.db import init_db
        from app.services import conversations as conversations_service
        from app.services import goals as goals_service

        init_db()  # 这条用例不建 app, 表要自己建

        conversation_id = conversations_service.ensure_conversation("qq", "private", "3807050597")
        origin_id = conversations_service.ensure_conversation("desktop", "thread", "origin-thread")
        goal = goals_service.create_goal(origin_id, "帮我问一下 km 今晚有没有空打游戏")
        goals_service.link_conversation(goal["id"], conversation_id)

        # 走真实的 retrieve → 拿到 working_memory, 再交给 reason 组提示词
        state = {"conversation_id": conversation_id, "event": {"text": "有的"}, "messages": []}
        memory = (await retrieve_node.run(state))["working_memory"]
        assert memory["goal_is_here"] is False

        system = _build_messages({"working_memory": memory, "messages": []}, "- send_qq_message")[0].content
        assert "号主私下交代你办的事" in system
        assert "不要念给对方" in system
        assert "对方才是当事人" in system.replace("\n", "")
        # 关键: 不能出现"当前目标:"这种号主视角的措辞
        assert "当前目标:" not in system

        # 反向: 目标就挂在本会话时, 仍按"当前目标"给(控制台自己的活)
        goals_service.create_goal(conversation_id, "在本会话里办的事")
        state = {"conversation_id": conversation_id, "event": {"text": "在吗"}, "messages": []}
        memory = (await retrieve_node.run(state))["working_memory"]
        assert memory["goal_is_here"] is True
        system = _build_messages({"working_memory": memory, "messages": []}, "- send_qq_message")[0].content
        assert "当前目标:" in system


# ---------------------------------------------------------------------------
# 两道确定性闸门(提示词之外的安全网)
# ---------------------------------------------------------------------------
# 真机第二/第三次事故证明: 提示词管不住的时候必须物理拦住 ——
#   ① agent 在 km 的会话里用 send_qq_message 又发了一遍(对方收到两条重复的);
#   ② finalize 把"那行, 我问他几点开始, 等他定个时间"发给了对方(内部口径外泄)。
class TestOutboundGuards:
    async def test_cannot_message_the_person_im_chatting_with(self, make_client, monkeypatch):
        """① 对方就在当前会话 ⇒ 工具直接拒绝, 让他"直接回复"。"""
        from app.tools import messaging

        client = make_client(["好的"])
        contact = client.post(
            "/api/conversations/resolve",
            json={"platform": "qq", "chat_type": "private", "external_id": "3807050597"},
        ).json()

        sent: list[tuple] = []

        async def fake_sender(*args):
            sent.append(args)
            return True

        monkeypatch.setattr(messaging, "_sender", fake_sender)

        result = await messaging.send_qq_message.ainvoke(
            {"chat_id": "3807050597", "text": "那今晚几点开?", "chat_type": "private"},
            config={"configurable": {"thread_id": contact["id"]}},
        )
        assert "对方就在当前会话里" in result
        assert sent == [], "不该真的发出去"

        # 反向: 发给**别人**照旧允许(别把闸门做成"什么都发不了")
        other = await messaging.send_qq_message.ainvoke(
            {"chat_id": "2597164807", "text": "帮我带个话", "chat_type": "private"},
            config={"configurable": {"thread_id": contact["id"]}},
        )
        assert "已发送" in other and len(sent) == 1

    def test_internal_wording_is_held_back_from_contact(self, make_client):
        """② 发给联系人的话命中内部口径 ⇒ 不发出去, 挂回控制台等号主确认。"""
        client = make_client(
            [
                ("send_qq_message", {"chat_id": "3807050597", "text": "今晚有空打游戏吗"}),
                "已经问过了,等他回。",
                # 真机泄露原句: 对方是 km, 那个"他"是号主 —— 外人一听就知道有代理
                "那行，我问他几点开始，等他定个时间。",
            ]
        )
        thread_id, contact_id = _link_contact(client)

        resp = client.post(
            "/qq/webhook",
            json={
                "post_type": "message",
                "message_type": "private",
                "user_id": 3807050597,
                "message_id": 60002,
                "sender": {"nickname": "㎞"},
                "message": [{"type": "text", "data": {"text": "有啊"}}],
            },
        )
        assert resp.status_code == 200

        # 那句内部口径**不能**出现在对方会话里
        contact_messages = client.get(f"/api/conversations/{contact_id}/messages").json()
        assert not any("等他定" in m["content"] for m in contact_messages), contact_messages

        # 而要挂回控制台等号主确认
        console_messages = client.get(f"/api/threads/{thread_id}/messages").json()
        held = [m for m in console_messages if "【需确认】" in m["content"]]
        assert held, console_messages
        assert "等他定" in held[-1]["content"]
