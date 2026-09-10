# -*- coding: utf-8 -*-
"""异步转告任务验证(帮问 A,再转告 B)。

这是用户明确要的核心场景: "帮我问一下 XX,然后转告一下 XX"。

链路:
  1. 主 agent 派 task → 建 goal → 跑图
  2. agent 用 send_qq_message 去问 A(消息落在 A 的会话里)
  3. A 回复 → 这条回复必须能回到 agent 手里,任务才能继续
  4. agent 转告 B

第 3 步是最容易断的一环: A 的回复落在 **A 的会话**,而 goal 挂在别处。
本文件用来验证(并守住)这条链路。

不联网: LLM 与发送器都是假的。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult


class ScriptedModel(BaseChatModel):
    """按剧本行动的假模型: 第一轮调工具,之后直接回复。

    用一个模块级队列控制"本次返回什么",这样能精确模拟
    "先发消息问人,拿到结果后再总结回复"的两段式行为。
    """

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        step = ScriptedModel.script.pop(0) if ScriptedModel.script else "好的"
        ScriptedModel.prompts.append("\n".join(str(getattr(m, "content", "")) for m in messages))

        if isinstance(step, dict):
            message = AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": step["tool"],
                        "args": step["args"],
                        "id": f"call-{len(ScriptedModel.prompts)}",
                    }
                ],
            )
        else:
            message = AIMessage(content=str(step))
        return ChatResult(generations=[ChatGeneration(message=message)])

    def bind_tools(self, tools, **kwargs):
        return self


ScriptedModel.script: list = []
ScriptedModel.prompts: list[str] = []


@pytest.fixture()
def env(temp_data_dir, monkeypatch):
    """真实应用 + 假模型 + 空传输(不碰网络)。

    关键: **保留真实的 contact_sender** —— 它负责"发出前先落库",
    换成假发送器会让落库这步被跳过,测出来的就不是真实行为了。
    网络层通过替换 runtime sender 来空转。
    """
    monkeypatch.setenv("MEMO_ECHO_WEBHOOK_ASYNC", "false")
    monkeypatch.setenv("MEMO_ECHO_API_TOKEN", "")
    monkeypatch.setenv("MEMO_ECHO_BOT_QQ", "3969785168")

    from app import config as config_module

    monkeypatch.setattr(config_module, "_settings", None, raising=False)

    ScriptedModel.script = []
    ScriptedModel.prompts = []

    import app.main as main_module

    app = main_module.create_app()

    from app.agent import runtime as agent_runtime
    from app.agent.runtime import get_graph

    graph = get_graph()
    graph.llm_factory = lambda fast=False: ScriptedModel()

    sent: list[tuple[str, str]] = []   # (conversation_id, text)
    next_platform_id = [5000]

    async def noop_sender(conversation_id: str, text: str, source: str) -> dict[str, Any]:
        """替代 NapCat 传输: 只记录，让落库逻辑照常执行。

        刻意返回 platform_message_id —— 真实 NapCat 发送接口会返回它，
        代码据此识别"自己发的消息被平台回显"。恒返回 None 会让这条机制失效，
        测出来的就不是真实行为。
        """
        sent.append((conversation_id, text))
        next_platform_id[0] += 1
        return {"ok": True, "platform_message_id": str(next_platform_id[0]), "error": ""}

    agent_runtime.set_sender(noop_sender)   # outbox 走这里
    graph.sender = noop_sender              # finalize 走这里

    with TestClient(app) as test_client:
        test_client.sent = sent  # type: ignore[attr-defined]
        yield test_client


def _webhook(client, user_id: int, message_id: int, text: str) -> None:
    client.post(
        "/qq/webhook",
        json={
            "post_type": "message",
            "message_type": "private",
            "user_id": user_id,
            "message_id": message_id,
            "message": [{"type": "text", "data": {"text": text}}],
        },
    )


# ---------------------------------------------------------------------------
# 场景一: 任务派发后,agent 主动去问别人(第一步能不能走通)
# ---------------------------------------------------------------------------
class TestAskOthers:
    def test_agent_can_message_another_contact(self, env):
        """agent 能通过工具给第三方发消息,并落进那个人的会话历史。"""
        from app.services import conversations as conversations_service

        # 第一轮调工具去问 km,第二轮收尾回复
        ScriptedModel.script = [
            {"tool": "send_qq_message", "args": {"chat_id": "20001", "text": "今晚几点上课?"}},
            "已经帮你问了,等他回",
        ]

        env.post(
            "/api/dispatch",
            json={
                "caller": "main-agent",
                "kind": "task",
                "target": {"platform": "qq", "chat_type": "private", "external_id": "10086"},
                "instruction": "问一下 km 今晚几点上课",
            },
        )

        # 工具确实发出去了(消息文本能对上)
        assert any("今晚几点上课" in text for _, text in env.sent), f"没有发出消息: {env.sent}"

        # 且 km 的会话里留下了出站记录(这样对方回复时上下文对得上)
        km_id = conversations_service.ensure_conversation("qq", "private", "20001")
        km_msgs = conversations_service.list_messages(km_id, limit=10)
        assert [m["role"] for m in km_msgs] == ["assistant"], f"km 会话历史异常: {km_msgs}"
        assert "今晚几点上课" in km_msgs[0]["content"]


# ---------------------------------------------------------------------------
# 场景二: 对方回复后,任务能不能继续(最容易断的一环)
# ---------------------------------------------------------------------------
class TestReplyComesBack:
    def test_reply_from_contact_reaches_agent(self, env):
        """核心: agent 问出去之后,对方的回复要能回到 agent 手里。"""
        from app.services import conversations as conversations_service
        from app.services import goals as goals_service

        ScriptedModel.script = [
            {"tool": "send_qq_message", "args": {"chat_id": "20001", "text": "今晚几点上课?"}},
            "已经帮你问了",
        ]

        env.post(
            "/api/dispatch",
            json={
                "caller": "main-agent",
                "kind": "task",
                "target": {"platform": "qq", "chat_type": "private", "external_id": "10086"},
                "instruction": "问一下 km 今晚几点上课",
            },
        )

        # 第一轮确实把问题发出去了
        assert any("今晚几点上课" in text for _, text in env.sent), f"没有问出去: {env.sent}"
        prompt_count_before = len(ScriptedModel.prompts)

        # km 回复了
        ScriptedModel.script = ["收到"]
        _webhook(env, 20001, 5001, "八点上课")

        # 关键断言: agent 被再次唤起(多了一次模型调用)
        assert len(ScriptedModel.prompts) > prompt_count_before, (
            "对方的回复没有回到 agent 手里 —— 异步任务会卡死在这里"
        )
        # 且这一次看得到对方说的话
        assert "八点上课" in ScriptedModel.prompts[-1], (
            f"agent 被唤起了但看不到回复内容:\n{ScriptedModel.prompts[-1]}"
        )


# ---------------------------------------------------------------------------
# 场景三: 自动回复开启 + 任务同时存在 —— 会不会重复
# ---------------------------------------------------------------------------
class TestNoDuplication:
    def test_task_and_auto_reply_do_not_double_reply(self, env):
        """会话已开自动回复,任务又落在这个会话: 一条消息只该得到一个回复。

        (重复回复是用户最容易察觉的故障 —— 对方会收到两条一样的话。)
        """
        from app.services import conversations as conversations_service
        from app.services import goals as goals_service
        from app.services import policy as policy_service

        conversation_id = conversations_service.ensure_conversation("qq", "private", "30001")
        policy_service.update_policy(conversation_id, reply_mode="auto")
        goals_service.create_goal(conversation_id, "问问对方周末有没有空")

        ScriptedModel.script = ["好呀", "好呀"]
        _webhook(env, 30001, 6001, "周末有空吗?")

        msgs = conversations_service.list_messages(conversation_id, limit=20)
        assistant_msgs = [m for m in msgs if m["role"] == "assistant"]
        assert len(assistant_msgs) == 1, f"一条消息产生了多条回复: {assistant_msgs}"

    def test_outbound_echo_does_not_duplicate_history(self, env):
        """我们发出去的消息,若平台回显(message_sent)再次进来,不该在历史里出现两遍。"""
        from app.services import conversations as conversations_service
        from app.services import policy as policy_service

        # 目标联系人的会话处于监视状态(会记录消息)
        km_id = conversations_service.ensure_conversation("qq", "private", "40001")
        policy_service.update_policy(km_id, monitor=True)

        ScriptedModel.script = [
            {"tool": "send_qq_message", "args": {"chat_id": "40001", "text": "在吗,问个事"}},
            "已发出",
        ]
        env.post(
            "/api/dispatch",
            json={
                "caller": "main-agent",
                "kind": "task",
                "target": {"platform": "qq", "chat_type": "private", "external_id": "10086"},
                "instruction": "问一下 km 在不在",
            },
        )

        before = conversations_service.list_messages(km_id, limit=20)
        assert len(before) == 1, f"发送后应有 1 条记录: {before}"
        platform_id = before[0]["platform_message_id"]
        assert platform_id, "发送后没有回填平台消息 ID(去重机制会失效)"

        # 平台把这条自发消息回显回来(同一个平台消息 ID)
        env.post(
            "/qq/webhook",
            json={
                "post_type": "message_sent",
                "message_type": "private",
                "user_id": 40001,
                "self_id": 3969785168,
                "message_id": int(platform_id),
                "message": [{"type": "text", "data": {"text": "在吗,问个事"}}],
            },
        )

        after = conversations_service.list_messages(km_id, limit=20)
        same_content = [m for m in after if "在吗,问个事" in str(m.get("content") or "")]
        assert len(same_content) == 1, (
            f"同一条出站消息在历史里出现了 {len(same_content)} 次: "
            f"{[(m['role'], m['source']) for m in same_content]}"
        )

    def test_manual_message_from_phone_still_recorded(self, env):
        """反向保证: 号主在手机上手动发的消息(无对应记录)仍要入库。

        这是监视功能存在的理由之一 —— 修去重时最容易误伤的就是它。
        """
        from app.services import conversations as conversations_service
        from app.services import policy as policy_service

        conversation_id = conversations_service.ensure_conversation("qq", "private", "50001")
        policy_service.update_policy(conversation_id, monitor=True)

        # 手机上手动发的: message_sent,但平台 ID 我们从未发过
        env.post(
            "/qq/webhook",
            json={
                "post_type": "message_sent",
                "message_type": "private",
                "user_id": 50001,
                "self_id": 3969785168,
                "message_id": 999999,
                "message": [{"type": "text", "data": {"text": "我自己在手机上发的"}}],
            },
        )

        msgs = conversations_service.list_messages(conversation_id, limit=10)
        assert [m["content"] for m in msgs] == ["我自己在手机上发的"], f"手动发的消息被漏掉了: {msgs}"
