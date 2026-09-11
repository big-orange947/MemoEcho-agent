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

import asyncio
from typing import Any, Awaitable, Callable

import aiosqlite
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph

from ..config import get_settings
from ..services import conversations as conversations_service
from ..services import policy as policy_service
from . import state as state_schema
from .nodes import act, finalize, ingest, reflect, retrieve, reason

# ---------------------------------------------------------------------------
# 类型别名
# ---------------------------------------------------------------------------
# 注入给图的"外部能力"：LLM 工厂、工具集、消息发送器。
# 这样图本身不直接依赖具体实现，main.py 组装时注入。
LlmFactory = Callable[[bool], BaseChatModel]      # fast: bool -> model
Sender = Callable[[str, str, str], Awaitable[None]]  # (conversation_id, text, source)

# 单个会话允许同时"在跑 + 排队"的事件数上限。
# 多个入口(QQ 消息 / 主 agent 调度 / 定时唤醒)可能同时打到同一会话,
# 超过上限说明该会话已经拥堵,此时应快速拒绝(返回 busy)而不是无限堆积 ——
# 队列里排着的每一条都要跑一次 LLM,堆太多只会让回复越来越旧。
MAX_INFLIGHT_PER_CONVERSATION = 5


class ConversationBusyError(RuntimeError):
    """会话繁忙: 排队事件数超过上限,本次事件未被处理。

    调用方(API 层)应据此返回 429,让上游稍后重试,而不是静静丢弃。
    """

    def __init__(self, conversation_id: str, limit: int) -> None:
        super().__init__(f"会话 {conversation_id} 繁忙(排队上限 {limit})")
        self.conversation_id = conversation_id
        self.limit = limit


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
        # checkpoint 初始化锁: 多个会话首次并发进来时,保证只初始化一次
        # (否则会创建多个 SQLite 连接并重复编译图)
        self._saver_lock = asyncio.Lock()

        # ---- 会话级串行化(见 run_event 注释) ----
        # _conversation_locks: 每个会话一把锁,保证同一会话的事件按顺序执行;
        # _inflight: 每个会话"在跑 + 排队"的事件计数,用于限流。
        # 两个字典都按需创建、空闲时回收(见 _release_conversation)。
        self._conversation_locks: dict[str, asyncio.Lock] = {}
        self._inflight: dict[str, int] = {}

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
            # 决策用主模型 + bind_tools(让 LLM 原生选择工具)。
            # 关键: 只把**本会话授权**的工具给模型 —— 未授权的工具它连看都看不到,
            # 这是权限的第一层(第二层在 act 节点与工具内部)。
            tools = self._tools_for(state)
            llm = self.llm_factory(fast=False)
            if tools:
                llm = llm.bind_tools(tools)
            return reason.run(state, llm, tools)

        async def _act(state: dict[str, Any]) -> dict[str, Any]:
            # act 是异步节点: 工具可能做异步 IO(如给联系人发消息),
            # 同步节点会被丢进线程池而拿不到事件循环。
            return await act.run(state, self.tools_by_name)

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

        # ingest 之后先做幂等判断: 重复消息直接结束,不进推理。
        # 这一步是"防重复回复"的关键 —— 平台重推同一条消息时,
        # 若不短路,agent 会重新推理并再发一次回复。
        builder.add_conditional_edges(
            "ingest",
            self._route_after_ingest,
            {"retrieve": "retrieve", "end": END},
        )
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
        """首次运行时创建 AsyncSqliteSaver 连接,并重新编译图(只执行一次)。

        并发安全: 用 _saver_lock 做双重检查 —— 多个会话首次同时进来时,
        只有第一个真正执行初始化,其余等到锁后直接返回。
        (若不加锁,会创建多个连接、重复编译图,连接还会泄漏。)
        """
        # 快路径: 已就绪直接返回(绝大多数调用走这里,不加锁开销)
        if self._saver_ready:
            return

        async with self._saver_lock:
            # 慢路径二次检查: 等锁期间可能已被其它协程初始化完成
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
    def _route_after_ingest(state: dict[str, Any]) -> str:
        """幂等判断: 重复消息直接结束,否则继续检索+推理。

        ingest 已把"是否重复"写进 state.duplicate:
          · True  → 回 "end"(到 END,不产生任何回复)
          · False → 回 "retrieve"(正常流程)
        """
        return "end" if state.get("duplicate") else "retrieve"

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
          {kind, source, platform, chat_type, external_id, text, sender_id, is_self, ...}

        流程:
          1. 确保 checkpoint 就绪(首次调用时惰性创建);
          2. 定位会话(查库/新建),得到 conversation_id;
          3. 按会话策略解析**本次可用的工具集**(权限收口,见 _tools_for);
          4. **会话级排队**: 同一会话的事件串行执行(见下方说明);
          5. 预置 state → 跑图 → 取最终回复。

        为什么要会话级串行化:
          同一会话的所有事件都写同一份 LangGraph checkpoint(thread_id =
          conversation_id)。若两条事件并发执行,checkpoint 会被交错写入,
          导致上下文错乱、回复互相覆盖。加入主 agent 调度后,QQ 消息、
          调度指令、定时唤醒三种入口完全可能同时打到同一会话,因此必须排队。

        为什么要限流(MAX_INFLIGHT_PER_CONVERSATION):
          队列里每条事件都要跑一次 LLM(数秒~数十秒)。若不设上限,
          拥堵会话会无限堆积,回复越来越滞后且看不到尽头。
          超过上限直接抛 ConversationBusyError,让 API 层返回 429。

        异常: ConversationBusyError(会话繁忙,事件未被处理)。
        """
        # ---- 1. 定位会话 ----
        # 先定位会话(同步 DB 操作,很快),因为限流计数需要会话 ID。
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

        # ---- 2. 解析本次可用的工具集(权限) ----
        # 放在这里而不是节点里: 工具集是"这次执行"的属性,随 state 传下去,
        # reason 据此 bind_tools、act 据此拒绝未授权调用 —— 两层都在同一条数据上。
        conversation = conversations_service.get_conversation(conversation_id) or {}
        allowed_tools = policy_service.resolve_allowed_tools(
            conversation, {tool.name: set(tool.tags or []) for tool in self.tools}
        )

        # ---- 3. 入队(计数 + 限流) ----
        # 注意: _inflight 在等待锁**之前**自增,所以它统计的是
        # "正在跑 + 排队中"的总数,这正是限流想要的口径。
        # 以下三行之间没有 await,在事件循环中是原子的,不会被并发打断。
        inflight = self._inflight.get(conversation_id, 0)
        if inflight >= MAX_INFLIGHT_PER_CONVERSATION:
            raise ConversationBusyError(conversation_id, MAX_INFLIGHT_PER_CONVERSATION)
        self._inflight[conversation_id] = inflight + 1

        lock = self._conversation_locks.get(conversation_id)
        if lock is None:
            lock = asyncio.Lock()
            self._conversation_locks[conversation_id] = lock

        try:
            async with lock:
                # ---- 4. 确保 checkpoint 就绪 ----
                # 放在锁内(且在计数之后): 一是并发初始化由 _saver_lock 保护,
                # 二是计数已经反映了真实排队情况 —— 若放在计数之前 await,
                # 首次初始化期间 is_busy() 会看不到排队中的任务。
                await self._ensure_checkpointer()
                # ---- 5. 跑图(同一会话内串行) ----
                return await self._run_graph(event, conversation_id, allowed_tools)
        finally:
            self._release_conversation(conversation_id)

    def _tools_for(self, state: dict[str, Any]) -> list[BaseTool]:
        """按 state 里的授权集合过滤工具(未解析时视为全量,兼容直接调用)。

        授权数据由 run_event 放进 state.allowed_tools。
        """
        names = state.get("allowed_tools")
        if names is None:
            return self.tools
        allowed = set(names)
        return [tool for tool in self.tools if tool.name in allowed]

    # ------------------------------------------------------------------ 内部执行
    async def _run_graph(
        self,
        event: dict[str, Any],
        conversation_id: str,
        allowed_tools: set[str] | None = None,
    ) -> str | None:
        """真正跑一次图(调用方已持有该会话的锁)。

        构造初始 state: LangGraph 会以 thread_id 从 checkpoint 恢复
        messages 等历史,这里只提供"本次新增的"字段。
        """
        initial: dict[str, Any] = {
            "conversation_id": conversation_id,
            "event": event,
            "goal": None,
            "working_memory": {},
            "tool_results": [],
            "decision": {},
            "output_text": "",
            # 本次可用的工具名(权限收口);None = 不限制
            "allowed_tools": sorted(allowed_tools) if allowed_tools is not None else None,
            # 请示暂停标记必须**每轮重置**: 它是"这一轮"的属性。
            # 不复位的话,上一次请示的残留会让后续每一轮都拒绝发送(而且无声无息)。
            "awaiting_owner": False,
        }

        result = await self.graph.ainvoke(
            initial,
            config={"configurable": {"thread_id": conversation_id}},
        )

        # 幂等短路: ingest 判定消息重复时图会直接结束。
        # 此时**不能**去读 messages 里的 AI 文本 —— 那是 checkpoint 恢复出来的
        # 历史回复(上一次处理留下的),读出来会被误当成"本次的回复"再发一遍,
        # 相当于绕过了幂等保护。所以这里显式返回 None。
        if result.get("duplicate"):
            return None

        # 最终回复 = 结果消息里最后一条 AI 文本(与 finalize 提取逻辑一致)
        for message in reversed(result.get("messages") or []):
            if getattr(message, "type", "") != "ai":
                continue
            text = str(getattr(message, "content", "") or "").strip()
            if text:
                return text
        return None

    def _release_conversation(self, conversation_id: str) -> None:
        """出队: 递减计数;该会话彻底空闲时回收锁与计数器。

        回收时机说明:
          _inflight 在"等待锁之前"就自增,因此当它降到 0 时,
          必然没有其它任务在跑或排队 —— 此刻移除锁是安全的
          (若随后有新任务到来,会重新创建一把新锁,不会与旧锁上的等待者错配)。
        """
        remaining = self._inflight.get(conversation_id, 0) - 1
        if remaining > 0:
            self._inflight[conversation_id] = remaining
            return

        # 彻底空闲: 清理两个字典,避免会话数增长后内存只增不减
        self._inflight.pop(conversation_id, None)
        self._conversation_locks.pop(conversation_id, None)

    # ------------------------------------------------------------------ 状态查询
    def busy_conversations(self) -> dict[str, int]:
        """返回当前有事件在跑/排队的会话及数量(供状态页与排障)。"""
        return dict(self._inflight)

    def is_busy(self, conversation_id: str) -> bool:
        """判断某会话是否已达排队上限(API 层据此提前返回 429)。

        说明: 这只是"预检",真正的上限判定在 run_event 里(权威)。
        预检的意义是让调用方立刻拿到 429,而不是等事件被静默丢弃。
        """
        return self._inflight.get(conversation_id, 0) >= MAX_INFLIGHT_PER_CONVERSATION
