# -*- coding: utf-8 -*-
"""HITL(人工介入)行为测试。

规则(用户明确):
1. 用自然语言下命令时,**默认**就是 HITL 模式(拿不准先请示);
2. 显式配置的会话可以**开关**它;
3. 但**无论开关如何**,提示词都要约束 agent 不越界
   —— 关掉只是"少问",不是"胆子变大"。

本文件守住这三条,以及一个更容易被忽略的点:
请示之后必须**真的**停下来(工具这么承诺了,代码要兜住 —— 只靠提示词不算数)。
"""
from __future__ import annotations

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from app.agent.prompts import HITL_OFF_RULES, HITL_ON_RULES, REASON_SYSTEM_PROMPT
from app.services import policy as policy_service


@pytest.fixture()
def env(temp_data_dir):
    from app.db import init_db

    init_db()
    return None


class PromptCapturingModel(BaseChatModel):
    """记录每次收到的完整提示词的假模型。"""

    prompts: list[str] = []

    @property
    def _llm_type(self) -> str:
        return "prompt-capturing"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        PromptCapturingModel.prompts.append(
            "\n".join(str(getattr(m, "content", "")) for m in messages)
        )
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="好的"))])

    def bind_tools(self, tools, **kwargs):
        return self


# ---------------------------------------------------------------------------
# 1. 默认开启
# ---------------------------------------------------------------------------
class TestDefaultOn:
    def test_new_conversation_defaults_to_hitl(self, env):
        """新建会话默认要求请示 —— 用自然语言下命令不需要额外配置。"""
        from app.services import conversations as conversations_service

        conversations_service.ensure_conversation("qq", "private", "10001")
        conversation = conversations_service.find_conversation("qq", "private", "10001")

        assert policy_service.resolve_hitl(conversation) is True

    def test_schema_default(self, env):
        """库里的默认值也是 1(老库补列同样如此)。"""
        from app.db import get_connection
        from app.services import conversations as conversations_service

        conversations_service.ensure_conversation("qq", "private", "10002")
        row = get_connection().execute(
            "SELECT require_human_confirmation FROM conversations WHERE external_id='10002'"
        ).fetchone()
        assert int(row["require_human_confirmation"]) == 1


# ---------------------------------------------------------------------------
# 2. 可开关
# ---------------------------------------------------------------------------
class TestToggle:
    def test_can_turn_off(self, env):
        from app.services import conversations as conversations_service

        conversation_id = conversations_service.ensure_conversation("qq", "private", "10003")
        policy_service.update_policy(conversation_id, require_human_confirmation=0)

        conversation = conversations_service.get_conversation(conversation_id)
        assert policy_service.resolve_hitl(conversation) is False

    def test_can_turn_back_on(self, env):
        from app.services import conversations as conversations_service

        conversation_id = conversations_service.ensure_conversation("qq", "private", "10004")
        policy_service.update_policy(conversation_id, require_human_confirmation=0)
        policy_service.update_policy(conversation_id, require_human_confirmation=1)

        conversation = conversations_service.get_conversation(conversation_id)
        assert policy_service.resolve_hitl(conversation) is True


# ---------------------------------------------------------------------------
# 3. 提示词: 开关影响"授权范围",底线任何情况都在
# ---------------------------------------------------------------------------
class TestPrompt:
    def test_on_variant_instructs_to_ask(self, env):
        from app.services import conversations as conversations_service

        conversations_service.ensure_conversation("qq", "private", "10005")
        conversation = conversations_service.find_conversation("qq", "private", "10005")

        prompt = REASON_SYSTEM_PROMPT.format(
            tools_description="- wait: 等一会",
            hitl_rules=HITL_ON_RULES if policy_service.resolve_hitl(conversation) else HITL_OFF_RULES,
        )
        assert "拿不准的事不要自己拍板" in prompt
        assert "escalate_to_owner" in prompt
        assert "已开启" in prompt

    def test_off_variant_still_keeps_baseline(self, env):
        """关掉请示后: 授权范围变宽,但**底线必须还在**。"""
        prompt = REASON_SYSTEM_PROMPT.format(
            tools_description="- wait: 等一会",
            hitl_rules=HITL_OFF_RULES,
        )
        # 授权范围: 范围内自主
        assert "不必事事请示" in prompt
        # 底线: 任何情况下都在
        assert "底线" in prompt
        assert "超出授权的承诺" in prompt
        assert "不编造" in prompt
        assert "重大决定" in prompt

    def test_baseline_present_in_both_variants(self):
        """两种变体都要带上底线 —— 这是最容易在改动中丢掉的约束。"""
        for rules in (HITL_ON_RULES, HITL_OFF_RULES):
            prompt = REASON_SYSTEM_PROMPT.format(tools_description="", hitl_rules=rules)
            assert "不替号主做超出授权的承诺" in prompt
            assert "不编造" in prompt


class TestPromptWiring:
    @pytest.mark.asyncio
    async def test_hitl_flag_reaches_prompt(self, env):
        """端到端: 会话配置真的改变了模型收到的提示词。"""
        from app.agent.graph import AgentGraph
        from app.services import conversations as conversations_service

        conversation_id = conversations_service.ensure_conversation("qq", "private", "10006")

        async def _sender(conversation_id: str, text: str, source: str) -> None:
            return None

        PromptCapturingModel.prompts = []
        graph = AgentGraph(llm_factory=lambda fast=False: PromptCapturingModel(), tools=[], sender=_sender)
        try:
            # 默认(开): 提示词里是"请示要求"
            await graph.run_event(
                {
                    "conversation_id": conversation_id,
                    "kind": "message",
                    "platform": "qq",
                    "chat_type": "private",
                    "external_id": "10006",
                    "text": "在吗",
                    "is_self": False,
                }
            )
            assert "拿不准的事不要自己拍板" in PromptCapturingModel.prompts[-1]
            assert "底线" in PromptCapturingModel.prompts[-1]

            # 关掉之后: 提示词换成"自主度",但底线仍在
            policy_service.update_policy(conversation_id, require_human_confirmation=0)
            await graph.run_event(
                {
                    "conversation_id": conversation_id,
                    "kind": "message",
                    "platform": "qq",
                    "chat_type": "private",
                    "external_id": "10006",
                    "text": "再问一句",
                    "is_self": False,
                }
            )
            latest = PromptCapturingModel.prompts[-1]
            assert "不必事事请示" in latest
            assert "拿不准的事不要自己拍板" not in latest
            assert "不替号主做超出授权的承诺" in latest, "关掉请示后底线丢了"
        finally:
            await graph.close()


# ---------------------------------------------------------------------------
# 4. 请示后必须真的停下
# ---------------------------------------------------------------------------
class TestEscalationPauses:
    @pytest.mark.asyncio
    async def test_act_marks_awaiting_owner(self, env):
        """act 检测到成功请示 → 标记本轮暂停。"""
        from app.agent.nodes import act
        from app.tools.escalate import escalate_to_owner

        ai = AIMessage(
            content="",
            tool_calls=[
                {"name": "escalate_to_owner", "args": {"question": "改九点行吗?"}, "id": "c1"}
            ],
        )
        result = await act.run(
            {"conversation_id": "conv-hitl", "messages": [ai], "allowed_tools": None},
            {"escalate_to_owner": escalate_to_owner},
        )
        assert result.get("awaiting_owner") is True

    @pytest.mark.asyncio
    async def test_failed_escalation_does_not_pause(self, env):
        """请示失败(没登记上)不该暂停 —— 那会白白卡住任务。"""
        from app.agent.nodes import act
        from app.tools.escalate import escalate_to_owner

        ai = AIMessage(
            content="",
            tool_calls=[{"name": "escalate_to_owner", "args": {"question": "  "}, "id": "c1"}],
        )
        result = await act.run(
            {"conversation_id": "conv-hitl", "messages": [ai], "allowed_tools": None},
            {"escalate_to_owner": escalate_to_owner},
        )
        assert not result.get("awaiting_owner")

    @pytest.mark.asyncio
    async def test_finalize_does_not_send_after_escalation(self, env, monkeypatch):
        """核心: 请示之后,模型那句顺手的回复**不能**发出去。"""
        from app.agent.nodes import finalize
        from langchain_core.messages import AIMessage as Msg

        sent: list[tuple[str, str]] = []

        async def fake_sender(conversation_id: str, text: str, source: str):
            sent.append((conversation_id, text))
            return {"ok": True, "platform_message_id": "1", "error": ""}

        state = {
            "conversation_id": "conv-pause",
            "messages": [Msg(content="好,我先问一下他")],
            "decision": {},
            "goal": None,
            "awaiting_owner": True,
        }
        await finalize.run(state, fake_sender)

        assert sent == [], f"请示后仍然发出去了: {sent}"

    @pytest.mark.asyncio
    async def test_finalize_does_not_record_unsent_reply(self, env):
        """没发出去的回复也不能记进历史 —— 否则 agent 会以为它说过了。"""
        from app.agent.nodes import finalize
        from app.services import conversations as conversations_service
        from langchain_core.messages import AIMessage as Msg

        conversation_id = conversations_service.ensure_conversation("qq", "private", "10007")

        async def fake_sender(conversation_id: str, text: str, source: str):
            return {"ok": True, "platform_message_id": "1", "error": ""}

        await finalize.run(
            {
                "conversation_id": conversation_id,
                "messages": [Msg(content="我先问一下")],
                "decision": {},
                "goal": None,
                "awaiting_owner": True,
            },
            fake_sender,
        )

        assert conversations_service.list_messages(conversation_id, limit=10) == []

    @pytest.mark.asyncio
    async def test_flag_resets_next_turn(self, env):
        """暂停标记是"这一轮"的属性: 下一轮不该继续被它挡住。"""
        from app.agent.graph import AgentGraph
        from app.tools.escalate import escalate_to_owner

        conversation_id = "conv-reset"
        sent: list[str] = []

        async def _sender(cid: str, text: str, source: str):
            sent.append(text)
            return {"ok": True, "platform_message_id": "1", "error": ""}

        # 第一次: 模型先请示,然后顺手写一句话(不该发出去)
        class EscalatingModel(BaseChatModel):
            @property
            def _llm_type(self) -> str:
                return "escalating"

            def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
                if any(getattr(m, "type", "") == "tool" for m in messages):
                    return ChatResult(generations=[ChatGeneration(message=AIMessage(content="好,我先问一下"))])
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

        graph = AgentGraph(
            llm_factory=lambda fast=False: EscalatingModel(),
            tools=[escalate_to_owner],
            sender=_sender,
        )
        try:
            await graph.run_event(
                {
                    "conversation_id": conversation_id,
                    "kind": "message",
                    "platform": "desktop",
                    "chat_type": "thread",
                    "external_id": conversation_id,
                    "text": "帮我约他",
                    "is_self": False,
                }
            )
            assert sent == [], f"请示那轮不该发送: {sent}"

            # 第二次: 普通回复,应当正常发出(标记已重置)
            class PlainModel(BaseChatModel):
                @property
                def _llm_type(self) -> str:
                    return "plain"

                def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
                    return ChatResult(generations=[ChatGeneration(message=AIMessage(content="好的"))])

                def bind_tools(self, tools, **kwargs):
                    return self

            graph.llm_factory = lambda fast=False: PlainModel()
            await graph.run_event(
                {
                    "conversation_id": conversation_id,
                    "kind": "message",
                    "platform": "desktop",
                    "chat_type": "thread",
                    "external_id": conversation_id,
                    "text": "在吗",
                    "is_self": False,
                }
            )
            assert sent == ["好的"], f"下一轮被上一轮的标记挡住了: {sent}"
        finally:
            await graph.close()
