# -*- coding: utf-8 -*-
"""图执行与并发控制测试。

用 fake LLM(不联网)覆盖:
- 六节点图跑通(单轮回复);
- 会话级串行化(同会话并发不交错);
- 队列上限(超限抛 ConversationBusyError);
- timer 事件不写入对话历史。

fake 的方式: 实现一个最小的 BaseChatModel,invoke 时返回预设的 AIMessage。
这样 reason 节点的 bind_tools/invoke 都能工作,但完全不产生网络请求。
"""
from __future__ import annotations

import asyncio

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from app.agent.graph import MAX_INFLIGHT_PER_CONVERSATION, AgentGraph, ConversationBusyError


class FakeChatModel(BaseChatModel):
    """按预设文本返回的假模型(每次调用取下一个,用完复用最后一个)。

    用途: 让 reason / reflect 节点有稳定输出,测试不依赖真实模型。
    """

    responses: list[str] = []
    call_count: int = 0
    # 记录每次收到的消息,便于断言"上下文里有没有东西"
    seen_messages: list[list[BaseMessage]] = []

    @property
    def _llm_type(self) -> str:
        return "fake"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        FakeChatModel.call_count += 1
        FakeChatModel.seen_messages.append(list(messages))

        idx = min(len(FakeChatModel.seen_messages) - 1, len(self.responses) - 1)
        text = self.responses[idx] if self.responses else "好的"

        message = AIMessage(content=text)
        return ChatResult(generations=[ChatGeneration(message=message)])

    def bind_tools(self, tools, **kwargs):  # noqa: D102 - 测试中不需要真实绑定
        return self


@pytest.fixture()
def fake_llm_factory():
    """返回 (工厂函数, fake 类) —— 工厂产出同一个 fake 实例。"""
    FakeChatModel.call_count = 0
    FakeChatModel.seen_messages = []

    instance = FakeChatModel(responses=["好的，我知道了"])

    def factory(fast: bool = False):
        return instance

    return factory, FakeChatModel


@pytest.fixture()
def graph_env(temp_data_dir, fake_llm_factory):
    """准备好数据目录 + 图的执行环境,返回 (graph 实例, fake 类)。"""
    from app.db import init_db

    init_db()

    factory, fake_cls = fake_llm_factory

    async def sender(conversation_id: str, text: str, source: str) -> None:
        """发送器: 测试中只记录,不真发。"""
        sent.append((conversation_id, text))

    sent: list[tuple[str, str]] = []

    graph = AgentGraph(llm_factory=factory, tools=[], sender=sender)
    graph.sent = sent  # 挂上便于断言
    return graph, fake_cls


# ---------------------------------------------------------------------------
# 基本执行
# ---------------------------------------------------------------------------
class TestGraphFlow:
    @pytest.mark.asyncio
    async def test_single_turn_reply(self, graph_env):
        """单轮消息 → 走完图 → 返回回复 / 落库 / 调用发送器。"""
        graph, fake_cls = graph_env

        reply = await graph.run_event(
            {
                "conversation_id": "conv-single",
                "kind": "message",
                "platform": "desktop",
                "chat_type": "thread",
                "external_id": "conv-single",
                "text": "你好",
                "sender_id": "tester",
                "is_self": False,
            }
        )

        assert reply == "好的，我知道了"
        # 消息落库(入站 + 出站)
        from app.services import conversations as convs

        msgs = convs.list_messages("conv-single")
        assert [m["role"] for m in msgs] == ["user", "assistant"]
        assert msgs[0]["content"] == "你好"
        # 发送器被调用
        assert len(graph.sent) == 1

    @pytest.mark.asyncio
    async def test_timer_event_not_in_history(self, graph_env):
        """timer 事件不写入对话历史(它不是"人说的话")。"""
        graph, _ = graph_env

        await graph.run_event(
            {
                "conversation_id": "conv-timer",
                "kind": "timer",
                "platform": "desktop",
                "chat_type": "thread",
                "external_id": "conv-timer",
                "text": "提醒喝水",
                "is_self": False,
            }
        )

        from app.services import conversations as convs

        msgs = convs.list_messages("conv-timer")
        # 只有 agent 的回复,没有把 "提醒喝水" 当成 user 消息
        assert all(m["content"] != "提醒喝水" for m in msgs)
        assert [m["role"] for m in msgs] == ["assistant"]


# ---------------------------------------------------------------------------
# 会话级串行化
# ---------------------------------------------------------------------------
class TestConcurrency:
    @pytest.mark.asyncio
    async def test_same_conversation_serialized(self, temp_data_dir, fake_llm_factory):
        """同一会话的并发事件必须串行执行(不交错)。"""
        from app.db import init_db

        init_db()
        factory, _ = fake_llm_factory

        # 记录每个事件从进入 _run_graph 到结束的顺序
        timeline: list[str] = []

        async def sender(conversation_id: str, text: str, source: str) -> None:
            return None

        graph = AgentGraph(llm_factory=factory, tools=[], sender=sender)

        original_run = graph._run_graph

        async def instrumented(event, conversation_id):
            timeline.append(f"start:{event['text']}")
            # 人为拉长执行时间,确保并发时能观察到交错
            await asyncio.sleep(0.05)
            result = await original_run(event, conversation_id)
            timeline.append(f"end:{event['text']}")
            return result

        graph._run_graph = instrumented  # type: ignore[method-assign]

        # 同一会话并发投递 3 条
        await asyncio.gather(
            *[
                graph.run_event(
                    {
                        "conversation_id": "conv-serial",
                        "kind": "message",
                        "platform": "desktop",
                        "chat_type": "thread",
                        "external_id": "conv-serial",
                        "text": f"msg{i}",
                        "is_self": False,
                    }
                )
                for i in range(3)
            ]
        )

        # 断言"开始-结束"成对出现 —— 即无交错:
        # timeline 必须是 [start:X, end:X, start:Y, end:Y, ...] 的形态。
        # 注意: 不能断言 FIFO 顺序 —— asyncio.Lock 只保证互斥,
        # 不保证等待者按到达顺序被唤醒(唤醒顺序由事件循环决定)。
        assert len(timeline) == 6, f"事件数不对: {timeline}"
        for i in range(0, len(timeline), 2):
            start, end = timeline[i], timeline[i + 1]
            assert start.startswith("start:") and end.startswith("end:"), f"交错执行: {timeline}"
            assert start.split(":", 1)[1] == end.split(":", 1)[1], f"交错执行: {timeline}"
        # 三条消息都处理过
        assert {x.split(":", 1)[1] for x in timeline} == {"msg0", "msg1", "msg2"}

    @pytest.mark.asyncio
    async def test_different_conversations_parallel(self, temp_data_dir, fake_llm_factory):
        """不同会话之间不应互相阻塞(锁是按会话隔离的)。"""
        from app.db import init_db

        init_db()
        factory, _ = fake_llm_factory

        async def sender(conversation_id: str, text: str, source: str) -> None:
            return None

        graph = AgentGraph(llm_factory=factory, tools=[], sender=sender)

        timeline: list[str] = []
        original_run = graph._run_graph

        async def instrumented(event, conversation_id):
            timeline.append(f"start:{conversation_id}")
            await asyncio.sleep(0.05)
            result = await original_run(event, conversation_id)
            timeline.append(f"end:{conversation_id}")
            return result

        graph._run_graph = instrumented  # type: ignore[method-assign]

        await asyncio.gather(
            *[
                graph.run_event(
                    {
                        "conversation_id": f"conv-p{i}",
                        "kind": "message",
                        "platform": "desktop",
                        "chat_type": "thread",
                        "external_id": f"conv-p{i}",
                        "text": "hi",
                        "is_self": False,
                    }
                )
                for i in range(3)
            ]
        )

        # 三个 start 应该都在任何 end 之前(说明并行)
        starts = [i for i, x in enumerate(timeline) if x.startswith("start:")]
        first_end = next(i for i, x in enumerate(timeline) if x.startswith("end:"))
        assert len(starts) == 3
        assert first_end > max(starts) - 1 or first_end >= 1, f"未观察到并行: {timeline}"
        # 更强的断言: 至少有两个 start 在第一个 end 之前
        assert len([s for s in starts if s < first_end]) >= 2, f"未观察到并行: {timeline}"

    @pytest.mark.asyncio
    async def test_busy_limit_raises(self, temp_data_dir, fake_llm_factory):
        """排队超过上限时抛 ConversationBusyError(而不是无限堆积)。"""
        from app.db import init_db

        init_db()
        factory, _ = fake_llm_factory

        async def sender(conversation_id: str, text: str, source: str) -> None:
            return None

        graph = AgentGraph(llm_factory=factory, tools=[], sender=sender)

        # 让每个任务执行得足够慢,堆积到上限
        async def slow_run(event, conversation_id):
            await asyncio.sleep(0.3)
            return None

        graph._run_graph = slow_run  # type: ignore[method-assign]

        tasks = [
            graph.run_event(
                {
                    "conversation_id": "conv-busy",
                    "kind": "message",
                    "platform": "desktop",
                    "chat_type": "thread",
                    "external_id": "conv-busy",
                    "text": f"m{i}",
                    "is_self": False,
                }
            )
            for i in range(MAX_INFLIGHT_PER_CONVERSATION + 3)
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        busy_errors = [r for r in results if isinstance(r, ConversationBusyError)]
        assert len(busy_errors) >= 1, "超过上限时应拒绝部分事件"
        assert busy_errors[0].conversation_id == "conv-busy"

    @pytest.mark.asyncio
    async def test_is_busy_reflects_state(self, temp_data_dir, fake_llm_factory):
        """is_busy 能反映会话当前的拥堵状态(API 层预检用)。"""
        from app.db import init_db

        init_db()
        factory, _ = fake_llm_factory

        async def sender(conversation_id: str, text: str, source: str) -> None:
            return None

        graph = AgentGraph(llm_factory=factory, tools=[], sender=sender)

        assert graph.is_busy("conv-idle") is False

        async def slow_run(event, conversation_id):
            await asyncio.sleep(0.3)
            return None

        graph._run_graph = slow_run  # type: ignore[method-assign]

        tasks = [
            asyncio.create_task(
                graph.run_event(
                    {
                        "conversation_id": "conv-state",
                        "kind": "message",
                        "platform": "desktop",
                        "chat_type": "thread",
                        "external_id": "conv-state",
                        "text": "x",
                        "is_self": False,
                    }
                )
            )
            for _ in range(MAX_INFLIGHT_PER_CONVERSATION)
        ]
        await asyncio.sleep(0.05)  # 让它们进入队列

        assert graph.is_busy("conv-state") is True

        await asyncio.gather(*tasks, return_exceptions=True)
        assert graph.is_busy("conv-state") is False

    @pytest.mark.asyncio
    async def test_locks_are_released(self, temp_data_dir, fake_llm_factory):
        """会话处理完后,锁与计数应被回收(内存不无限增长)。"""
        from app.db import init_db

        init_db()
        factory, _ = fake_llm_factory

        async def sender(conversation_id: str, text: str, source: str) -> None:
            return None

        graph = AgentGraph(llm_factory=factory, tools=[], sender=sender)

        await graph.run_event(
            {
                "conversation_id": "conv-clean",
                "kind": "message",
                "platform": "desktop",
                "chat_type": "thread",
                "external_id": "conv-clean",
                "text": "hi",
                "is_self": False,
            }
        )

        assert graph.busy_conversations() == {}
        assert "conv-clean" not in graph._conversation_locks
