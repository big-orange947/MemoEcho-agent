# -*- coding: utf-8 -*-
"""工具权限与历史裁剪测试(agent 侧的两条硬约束)。

1. 权限: 未授权的工具**不给模型看**(reason 只 bind 授权的),也**不许执行**
   (act 拒绝),高危工具内部还有第三层兜底;
2. 历史: checkpoint 不能无限增长 —— 否则监视开启后每轮 token 成本无界上升。

两个都用 fake 模型,不联网。
"""
from __future__ import annotations

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool

from app.services.policy import HIGH_RISK_TAG


class RecordingModel(BaseChatModel):
    """记录"被 bind 了哪些工具"的假模型。"""

    reply: str = "好的"
    bound_tool_names: list[list[str]] = []

    @property
    def _llm_type(self) -> str:
        return "recording"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=self.reply))])

    def bind_tools(self, tools, **kwargs):
        names = sorted(getattr(t, "name", str(t)) for t in tools)
        RecordingModel.bound_tool_names.append(names)
        return self


@tool
def fake_high_risk(tool_arg: str = "") -> str:
    """高危工具(测试替身): 代表"以号主身份对外发消息"这类能力。"""
    return "高危工具已执行"


fake_high_risk.tags = [HIGH_RISK_TAG]


@tool
def fake_low_risk(tool_arg: str = "") -> str:
    """低危工具(测试替身): 代表查询类能力。"""
    return "低危工具已执行"


@pytest.fixture()
def env(temp_data_dir):
    from app.db import init_db

    init_db()
    RecordingModel.bound_tool_names = []
    return None


def _make_graph(sender=None):
    from app.agent.graph import AgentGraph

    async def _sender(conversation_id: str, text: str, source: str) -> None:
        return None

    model = RecordingModel(reply="好的")
    graph = AgentGraph(
        llm_factory=lambda fast=False: model,
        tools=[fake_high_risk, fake_low_risk],
        sender=sender or _sender,
    )
    return graph


# ---------------------------------------------------------------------------
# 权限: 第一层(模型看不到)
# ---------------------------------------------------------------------------
class TestReasonBindsOnlyAllowed:
    @pytest.mark.asyncio
    async def test_group_chat_hides_high_risk_tool(self, env):
        """群聊会话: 高危工具不出现在绑定列表里(模型根本看不到)。"""
        from app.services import conversations as conversations_service

        conversations_service.ensure_conversation("qq", "group", "55555")
        graph = _make_graph()

        await graph.run_event(
            {
                "kind": "message",
                "platform": "qq",
                "chat_type": "group",
                "external_id": "55555",
                "text": "@我 在吗",
                "is_self": False,
            }
        )

        assert RecordingModel.bound_tool_names, "reason 没有绑定工具"
        bound = set(RecordingModel.bound_tool_names[-1])
        assert "fake_high_risk" not in bound, f"群聊不该看到高危工具: {bound}"
        assert "fake_low_risk" in bound

    @pytest.mark.asyncio
    async def test_private_chat_keeps_all_tools(self, env):
        """私聊: 默认不限制,高危工具照常可用(现有流程不回归)。"""
        graph = _make_graph()

        await graph.run_event(
            {
                "kind": "message",
                "platform": "qq",
                "chat_type": "private",
                "external_id": "20001",
                "text": "帮我问下小号",
                "is_self": False,
            }
        )

        bound = set(RecordingModel.bound_tool_names[-1])
        assert bound == {"fake_high_risk", "fake_low_risk"}


# ---------------------------------------------------------------------------
# 权限: 第二层(act 拒绝执行)
# ---------------------------------------------------------------------------
class TestActRejectsUnauthorized:
    @pytest.mark.asyncio
    async def test_unauthorized_call_is_refused(self, env):
        """模型硬要调用未授权工具时,act 拒绝执行并留痕。"""
        from app.agent.nodes import act

        ai = AIMessage(
            content="",
            tool_calls=[{"name": "fake_high_risk", "args": {}, "id": "call-1"}],
        )
        result = await act.run(
            {
                "conversation_id": "conv-denied",
                "messages": [ai],
                "allowed_tools": ["fake_low_risk"],
            },
            {"fake_high_risk": fake_high_risk, "fake_low_risk": fake_low_risk},
        )

        messages = result["messages"]
        assert len(messages) == 1
        assert isinstance(messages[0], ToolMessage)
        assert "未授权" in messages[0].content
        assert "已执行" not in messages[0].content

    @pytest.mark.asyncio
    async def test_authorized_call_runs(self, env):
        from app.agent.nodes import act

        ai = AIMessage(
            content="",
            tool_calls=[{"name": "fake_low_risk", "args": {}, "id": "call-2"}],
        )
        result = await act.run(
            {"conversation_id": "conv-ok", "messages": [ai], "allowed_tools": ["fake_low_risk"]},
            {"fake_high_risk": fake_high_risk, "fake_low_risk": fake_low_risk},
        )
        assert "低危工具已执行" in result["messages"][0].content

    @pytest.mark.asyncio
    async def test_none_means_unrestricted(self, env):
        """allowed_tools=None(未解析)时不限制 —— 兼容直接调用。"""
        from app.agent.nodes import act

        ai = AIMessage(content="", tool_calls=[{"name": "fake_high_risk", "args": {}, "id": "c"}])
        result = await act.run(
            {"conversation_id": "conv-any", "messages": [ai], "allowed_tools": None},
            {"fake_high_risk": fake_high_risk},
        )
        assert "已执行" in result["messages"][0].content


# ---------------------------------------------------------------------------
# 历史裁剪: checkpoint 有界
# ---------------------------------------------------------------------------
class TestHistoryTrimming:
    @pytest.mark.asyncio
    async def test_checkpoint_stays_bounded(self, temp_data_dir, monkeypatch):
        """多轮对话后,checkpoint 里的消息数应稳定在窗口附近,而不是一直涨。"""
        monkeypatch.setenv("MEMO_ECHO_HISTORY_MAX_MESSAGES", "4")
        from app import config as config_module

        monkeypatch.setattr(config_module, "_settings", None, raising=False)

        from app.db import init_db

        init_db()
        graph = _make_graph()

        for index in range(6):
            await graph.run_event(
                {
                    "conversation_id": "conv-trim",
                    "kind": "message",
                    "platform": "desktop",
                    "chat_type": "thread",
                    "external_id": "conv-trim",
                    "text": f"第{index}条",
                    "is_self": False,
                }
            )

        snapshot = await graph.graph.aget_state({"configurable": {"thread_id": "conv-trim"}})
        messages = list(snapshot.values.get("messages") or [])
        assert len(messages) <= 6, f"历史未被裁剪: {len(messages)} 条"

        # 数据库里的完整历史不受影响(裁剪只动模型的工作区)
        from app.services import conversations as conversations_service

        stored = conversations_service.list_messages("conv-trim", limit=100)
        assert len(stored) == 12, f"数据库历史被误删: {len(stored)} 条"
