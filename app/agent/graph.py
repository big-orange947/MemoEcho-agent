# =============================================================================
# graph.py - LangGraph 图组装(代理层的核心)
# -----------------------------------------------------------------------------
# 职责: 把六类节点连成一张可执行的图,并接入 checkpoint。
#
# 图结构:
#   START → ingest → retrieve → reason
#                                ├─(有 tool_calls)→ act → reason  (循环)
#                                └─(无 tool_calls)→ reflect → finalize → END
#
# checkpoint(SqliteSaver): LangGraph 自动把每次执行后的 State 存进 SQLite。
# 下次同会话(thread_id)事件进来时,从 checkpoint 恢复历史,
# 这正是"事件驱动恢复"的实现 —— 不再需要 v1 的 dispatch/lease/requeue。
#
# 对外只有一个入口: run_event(bus_event)
#   - 内部: 找/建会话 → 决定 thread_id → graph.ainvoke
# =============================================================================

from __future__ import annotations

import uuid
from typing import Any, Awaitable, Callable

import aiosqlite
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph

from ..config import get_settings
from ..services import conversations as conversations_service
from . import state as state_schema
from .nodes import act, finalize, ingest, reflect, retrieve, reason

# ---------------------------------------------------------------------------
# 类型别名
# ---------------------------------------------------------------------------
# 注入给图的"外部能力"：LLM 工厂、工具集、消息发送器。
# 这样图本身不直接依赖具体实现，main.py 组装时注入。
LlmFactory = Callable[[bool], BaseChatModel]      # fast: bool -> model
Sender = Callable[[str, str, str], Awaitable[None]]  # (conversation_id, text, source)


class AgentGraph:
    """封装一张已编译的 LangGraph 图 + 一个会话入口。"""

    def __init__(
        self,
        llm_factory: LlmFactory,
        tools: list[BaseTool],
        sender: Sender,
    ) -> None:
        self.llm_factory = llm_factory
        self.tools = tools
        self.sender = sender
        # 工具按名字索引,act 节点用它查找
        self.tools_by_name: dict[str, BaseTool] = {t.name: t for t in tools}

        # checkpoint 连接相关状态(首次 run_event 时才创建,见 _ensure_checkpointer)
        self._saver_ready = False
        self._checkpoint_conn = None

        # 编译图(编译后才能执行)。先不挂 checkpoint,见 _build_with 注释
        self.graph = self._build_with(checkpointer=None)

    # ------------------------------------------------------------------ 构建
    def _build_with(self, checkpointer):
        """构建图: 注册节点 → 连边 → 编译。
        参数 checkpointer 可为 None(首次编译)或 AsyncSqliteSaver(挂上后编译)。
        """
        builder = StateGraph(state_schema.AgentState)

        # ---- 注册节点 ----
        builder.add_node("ingest", ingest.run)
        builder.add_node("retrieve", retrieve.run)

        # reason/act/reflect 需要注入外部依赖(llm/tools),用闭包包装
        def _reason(state: dict[str, Any]) -> dict[str, Any]:
            # 决策用主模型 + bind_tools(让 LLM 原生选择工具)
            llm = self.llm_factory(fast=False).bind_tools(self.tools)
            return reason.run(state, llm, self.tools)

        def _act(state: dict[str, Any]) -> dict[str, Any]:
            return act.run(state, self.tools_by_name)

        def _reflect(state: dict[str, Any]) -> dict[str, Any]:
            # 目标评估用 fast 通道(轻量模型,省成本)
            llm = self.llm_factory(fast=True)
            return reflect.run(state, llm)

        async def _finalize(state: dict[str, Any]) -> dict[str, Any]:
            return await finalize.run(state, self.sender)

        builder.add_node("reason", _reason)
        builder.add_node("act", _act)
        builder.add_node("reflect", _reflect)
        builder.add_node("finalize", _finalize)

        # ---- 连边(固定顺序) ----
        builder.add_edge(START, "ingest")
        builder.add_edge("ingest", "retrieve")
        builder.add_edge("retrieve", "reason")

        # ---- 条件边: reason 之后看有没有 tool_calls ----
        builder.add_conditional_edges(
            "reason",
            self._route_after_reason,   # 返回下一个节点名
            {"act": "act", "reflect": "reflect"},
        )
        # act 执行完工具后回到 reason 继续决策(ReAct 循环)
        builder.add_edge("act", "reason")
        # reflect 评估完目标后收尾
        builder.add_edge("reflect", "finalize")
        builder.add_edge("finalize", END)

        # ---- 编译 + checkpoint(惰性) ----
        # 注意: 首次编译时 checkpointer=None,真正的 AsyncSqliteSaver 连接
        # 在首次 run_event 时创建(见 _ensure_checkpointer)。原因:
        #   - 图在 asyncio 中执行,checkpoint 必须用 AsyncSqliteSaver;
        #   - 如果在导入期用 asyncio.run 建连接,aiosqlite 的后台线程是非
        #     daemon 的,会导致进程退出时被线程阻塞(挂死);
        #   - 惰性创建还能保证连接诞生在"真实事件循环"里,无 loop 错配风险。
        return builder.compile(checkpointer=checkpointer)

    # ------------------------------------------------------------------ 生命周期
    async def _ensure_checkpointer(self) -> None:
        """首次运行时创建 AsyncSqliteSaver 连接,并重新编译图(只执行一次)。"""
        if self._saver_ready:
            return

        # 此时必然运行在真实事件循环中(被 run_event 调用),可以安全 await
        conn = await aiosqlite.connect(str(get_settings().data_dir / "checkpoints.db"))
        saver = AsyncSqliteSaver(conn=conn)
        await saver.setup()  # 建 checkpoint 表(幂等)
        self._checkpoint_conn = conn

        # 带着 saver 重新编译图(节点结构不变,只是挂上 checkpoint)
        self.graph = self._build_with(checkpointer=saver)
        self._saver_ready = True

    async def close(self) -> None:
        """释放 checkpoint 连接(应用退出时调用)。"""
        conn = getattr(self, "_checkpoint_conn", None)
        if conn is not None:
            await conn.close()
        self._saver_ready = False

    # ------------------------------------------------------------------ 路由
    @staticmethod
    def _route_after_reason(state: dict[str, Any]) -> str:
        """看最近一条 AIMessage: 有 tool_calls 就去 act,否则去 reflect。"""
        for message in reversed(state.get("messages") or []):
            if hasattr(message, "tool_calls") and getattr(message, "tool_calls", None):
                return "act"
            # LangChain 消息对象没有 tool_calls 属性时为 None,视为纯回复
            if hasattr(message, "content") and str(getattr(message, "content", "")).strip():
                break
        return "reflect"

    # ------------------------------------------------------------------ 入口
    async def run_event(self, event: dict[str, Any]) -> str | None:
        """处理一个归一化事件,返回最终回复文本(可能为 None)。

        事件形态(与 events.Event 对齐):
          {platform, chat_type, external_id, text, sender_id, is_self, event_id, ...}

        流程:
          1. 定位会话(查库/新建),得到 conversation_id;
          2. 用 conversation_id 作 thread_id 恢复 checkpoint;
          3. 预置 state: conversation_id + event + 空工作记忆;
          4. 跑图,取最终回复。
        """
        # ---- 0. 确保 checkpoint 就绪 ----
        # 首次事件到来时创建 AsyncSqliteSaver 并重新编译图(见 _ensure_checkpointer)
        await self._ensure_checkpointer()

        # ---- 1. 定位会话 ----
        # 桌面端消息已携带会话 ID: 直接复用,并保底确保该行存在
        # (否则 messages 外键约束会拒绝写入);
        # QQ 消息按三元组查/建。
        conversation_id = event.get("conversation_id") or conversations_service.ensure_conversation(
            platform=event.get("platform", "qq"),
            chat_type=event.get("chat_type", "private"),
            external_id=event.get("external_id", ""),
        )
        if event.get("conversation_id"):
            conversations_service.ensure_conversation_by_id(conversation_id)

        # ---- 2. 构造初始 state ----
        # LangGraph 会以 thread_id 从 checkpoint 恢复 messages 等历史,
        # 我们只需提供"本次新增的"字段。
        initial: dict[str, Any] = {
            "conversation_id": conversation_id,
            "event": event,
            "goal": None,
            "working_memory": {},
            "tool_results": [],
            "decision": {},
            "output_text": "",
        }

        # ---- 3. 跑图 ----
        result = await self.graph.ainvoke(
            initial,
            config={"configurable": {"thread_id": conversation_id}},
        )
        # 最终回复 = 结果消息里最后一条 AI 文本(与 finalize 提取逻辑一致)
        for message in reversed(result.get("messages") or []):
            if getattr(message, "type", "") != "ai":
                continue
            text = str(getattr(message, "content", "") or "").strip()
            if text:
                return text
        return None
