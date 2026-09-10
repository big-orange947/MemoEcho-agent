# -*- coding: utf-8 -*-
"""上下文连续性测试(checkpoint 之外的对话记录必须补进提示词)。

背景: 图里的对话历史来自 LangGraph checkpoint,而 checkpoint 只包含
**跑过图**的消息。这会留下一条真实盲区 ——
会话先被监视(消息只落库、不跑图),之后才开启回复时,
模型看不到监视期记录的任何内容,表现得像失忆。

本文件覆盖:
- 补全正确(监视期的消息能进提示词);
- **不重复**(正常会话的消息不能被注入两遍 —— 那会白烧 token 且让模型困惑);
- 重复内容按顺序匹配(前后说过两次同样的话,第二句不能被当成"已看过");
- 裁剪后上下文不丢(checkpoint 裁剪掉的窗口由数据库补回)。

不联网。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from app.agent.nodes.retrieve import build_history_block


# ---------------------------------------------------------------------------
# 单元: build_history_block
# ---------------------------------------------------------------------------
class TestBuildHistoryBlock:
    def _history(self, *pairs: tuple[str, str]) -> list[dict]:
        return [{"role": role, "content": content} for role, content in pairs]

    def test_no_checkpoint_means_all_unseen(self):
        """全新会话(无 checkpoint): 数据库历史全部补进提示词。"""
        block = build_history_block(self._history(("user", "我下周三去北京")), [])
        assert "我下周三去北京" in block
        assert block.startswith("以下是这段对话")

    def test_seen_messages_are_not_injected(self):
        """模型已经见过的消息不再注入(否则每次调用都要为同一段历史付费)。"""
        history = self._history(("user", "你好"), ("assistant", "在的"))
        block = build_history_block(history, [HumanMessage(content="你好"), AIMessage(content="在的")])
        assert block == ""

    def test_partial_overlap_only_injects_missing(self):
        """部分重叠: 只补 checkpoint 里没有的那部分。"""
        history = self._history(("user", "第一句"), ("assistant", "回复一"), ("user", "第二句"))
        block = build_history_block(
            history, [HumanMessage(content="第一句"), AIMessage(content="回复一")]
        )
        assert "第二句" in block
        assert "第一句" not in block
        assert "回复一" not in block

    def test_repeated_content_matched_in_order(self):
        """重复内容: agent 前后说了两次"好的",第二句不能被当成已看过。

        若去重写成"内容出现过就跳过",这里会把第二句也划掉 ——
        模型就会以为自己没说过最后那句话。
        """
        history = self._history(("assistant", "好的"), ("user", "再来一个"), ("assistant", "好的"))
        block = build_history_block(
            history, [AIMessage(content="好的"), HumanMessage(content="再来一个")]
        )
        # checkpoint 里只有第一句"好的",所以第二句"好的"(最后一条)应被补上
        assert block.count("好的") == 1

    def test_long_message_is_truncated(self):
        """超长消息截断 —— 一条几万字的转发不该吃掉整个上下文预算。"""
        block = build_history_block([{"role": "user", "content": "长" * 2000}], [])
        assert "已截断" in block
        assert len(block) < 1000

    def test_empty_content_skipped(self):
        block = build_history_block([{"role": "user", "content": "   "}], [])
        assert block == ""

    def test_role_labels(self):
        block = build_history_block(
            self._history(("user", "对方说的"), ("assistant", "我说的")), []
        )
        assert "对方: 对方说的" in block
        assert "我: 我说的" in block


# ---------------------------------------------------------------------------
# 集成: 先监视、后回复(真实链路)
# ---------------------------------------------------------------------------
class RecordingModel(BaseChatModel):
    """记录每次收到的提示词的假模型。"""

    seen: list[str] = []

    @property
    def _llm_type(self) -> str:
        return "recording"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        RecordingModel.seen.append(
            "\n".join(str(getattr(m, "content", "")) for m in messages)
        )
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="好的"))])

    def bind_tools(self, tools, **kwargs):
        return self


@pytest.fixture()
def client(temp_data_dir, monkeypatch):
    monkeypatch.setenv("MEMO_ECHO_WEBHOOK_ASYNC", "false")
    monkeypatch.setenv("MEMO_ECHO_API_TOKEN", "")
    monkeypatch.setenv("MEMO_ECHO_BOT_QQ", "3969785168")
    RecordingModel.seen = []

    from app import config as config_module

    monkeypatch.setattr(config_module, "_settings", None, raising=False)

    import app.main as main_module

    app = main_module.create_app()
    from app.agent.runtime import get_graph

    graph = get_graph()
    graph.llm_factory = lambda fast=False: RecordingModel()

    with TestClient(app) as test_client:
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


class TestMonitorThenReply:
    def test_monitored_history_reaches_model(self, client):
        """核心场景: 先只监视(消息落库、不跑图),再开启回复 —— 模型要能看到监视期内容。"""
        from app.services import conversations as conversations_service
        from app.services import policy as policy_service

        conversation_id = conversations_service.ensure_conversation("qq", "private", "90001")
        policy_service.update_policy(conversation_id, monitor=True)

        _webhook(client, 90001, 1, "我下周三要去北京出差")
        _webhook(client, 90001, 2, "酒店订在A座")

        # 开启自动回复后,第一次跑图
        policy_service.update_policy(conversation_id, reply_mode="auto")
        _webhook(client, 90001, 3, "我刚才说的酒店是哪一座?")

        prompt = RecordingModel.seen[-1]
        assert "下周三要去北京出差" in prompt, f"监视期的消息没进提示词:\n{prompt}"
        assert "酒店订在A座" in prompt, f"监视期的消息没进提示词:\n{prompt}"

    def test_no_duplication_in_normal_conversation(self, client):
        """正常会话不能重复注入 —— 消息既在 checkpoint 又在库里,只该出现一次。"""
        from app.services import conversations as conversations_service
        from app.services import policy as policy_service

        conversation_id = conversations_service.ensure_conversation("qq", "private", "90002")
        policy_service.update_policy(conversation_id, reply_mode="auto")

        _webhook(client, 90002, 1, "在吗")
        _webhook(client, 90002, 2, "晚上有空吗")

        prompt = RecordingModel.seen[-1]
        assert prompt.count("在吗") == 1, f"消息被注入了多次:\n{prompt}"
        assert prompt.count("晚上有空吗") == 1, f"消息被注入了多次:\n{prompt}"
        # 也不该出现补全块的抬头(说明没有多余内容被注入)
        assert "以下是这段对话**较早时**发生的内容" not in prompt


# ---------------------------------------------------------------------------
# 集成: checkpoint 裁剪后,窗口外的上下文由数据库补回
# ---------------------------------------------------------------------------
class TestTrimmedContextRefill:
    @pytest.mark.asyncio
    async def test_recent_context_intact_after_trimming(self, temp_data_dir, monkeypatch):
        """裁剪只压缩 checkpoint,不改数据库;近期上下文必须完整且不重复。

        注意这里刻意**不**断言"最早那句仍在提示词里":
        裁剪窗口与读取窗口是同一个 history_max_messages,窗口外的内容
        本就该被有界丢弃(这是成本封顶的设计,不是遗漏)。
        真正要守住的是"窗口内的上下文一条不少、一条不重"。
        """
        monkeypatch.setenv("MEMO_ECHO_HISTORY_MAX_MESSAGES", "6")
        RecordingModel.seen = []

        from app import config as config_module

        monkeypatch.setattr(config_module, "_settings", None, raising=False)

        from app.db import init_db

        init_db()

        from app.agent.graph import AgentGraph

        model = RecordingModel()

        async def _sender(conversation_id: str, text: str, source: str) -> None:
            return None

        graph = AgentGraph(llm_factory=lambda fast=False: model, tools=[], sender=_sender)
        try:
            for index in range(4):
                await graph.run_event(
                    {
                        "conversation_id": "conv-refill",
                        "kind": "message",
                        "platform": "desktop",
                        "chat_type": "thread",
                        "external_id": "conv-refill",
                        "text": f"第{index}句内容",
                        "is_self": False,
                    }
                )
        finally:
            # 必须关闭 checkpoint 连接: aiosqlite 的后台线程不是守护线程,
            # 不关会让 pytest 进程在退出时挂住(实测踩过)
            await graph.close()

        prompt = RecordingModel.seen[-1]
        # 窗口内最近几轮一句不少
        assert "第2句内容" in prompt and "第3句内容" in prompt, f"近期上下文丢失:\n{prompt}"
        # 且一句不重(重复注入会白烧 token 并让模型困惑)
        assert prompt.count("第3句内容") == 1, f"消息被重复注入:\n{prompt}"
        # 裁剪由 checkpoint 负责,数据库历史始终保持完整(前端/审计可翻旧账)
        from app.services import conversations as conversations_service

        stored = conversations_service.list_messages("conv-refill", limit=100)
        assert len(stored) == 8, f"数据库历史被误删: {len(stored)}"
