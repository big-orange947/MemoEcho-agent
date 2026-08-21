from __future__ import annotations

"""
委托执行的固定主链路（LangGraph）。

这条图是主控台委托任务事件执行的唯一认知链路：

    ingest_event -> hydrate_context -> reconcile_workflow -> select_ready_step
        -> react(ReAct/tools) -> review -> persist_transition -> wait/end

- ingest_event      把当前入站事件写入 L0 current_event，保证当前事件不丢失。
- hydrate_context   按步骤会话范围与起点水位组装一次性可信上下文（context envelope）。
- reconcile_workflow 认领事件租约、识别重复事件，并校验工作流/步骤仍可推进。
- select_ready_step 判断当前事件是否驱动该步骤（属于步骤会话且为消息/激活事件）。
- react             ReAct 决策（复用 DelegatedTaskWorkflow 的规划/观察/重试闭环）。
- review            候选回复的外部安全检查（CandidateReplyGuard 等）。
- persist_transition 持久化 WAIT/COMPLETE 转换；SEND 则交回编排层执行真实发送。
- wait/end          终态节点。

LangGraph Checkpointer 只保存运行快照：以 workflowId 作为 thread key，
支持恢复图执行，但不能代替 Event Center 的聊天事件与业务状态。
"""

import asyncio
import json
import logging
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from app.clients.event_center_service import EventCenterServiceClient
from app.schemas.delegated_tasks import DelegatedTaskActionDecision, DelegatedTaskActionInput
from app.schemas.events import UnifiedEvent
from app.services.delegated_task_context import DelegatedTaskContextAssembler
from app.services.message_identity import canonical_message_identity, is_runtime_generated_message


logger = logging.getLogger(__name__)


class DelegatedWorkflowFactsMissingError(RuntimeError):
    """父步骤声明事实不足；这是可恢复的业务等待状态，不是基础设施故障。"""

    def __init__(self, missing_facts: list[str]) -> None:
        self.missing_facts = tuple(missing_facts)
        super().__init__("父工作流步骤缺少声明事实: " + ", ".join(missing_facts))


class DelegatedExecutionState(TypedDict, total=False):
    """固定主链路图在节点之间传递的执行状态。"""

    event: dict[str, Any]
    task: dict[str, Any]
    model_profile: Any
    workflow_id: str
    claim_token: str
    context_envelope: dict[str, Any]
    reconcile_status: str        # PROCEED / CLAIM_UNAVAILABLE / DEDUPLICATED
    route: str                   # proceed / skip / send / done / retry / claim_unavailable
    decision: dict[str, Any]
    transition: dict[str, Any]
    persisted: bool
    write_back_actions: list[str]
    # finalize：SEND 后状态写回模式。编排层已执行真实发送，图只按给定决策落库，
    # 跳过 react/review，并把 SEND_AND_COMPLETE 当作完成转换持久化。
    finalize: bool
    error: str


class DelegatedExecutionGraph:
    """委托任务事件执行的固定主链路图。"""

    def __init__(
        self,
        *,
        delegated_task_workflow: Any,
        event_center_client: EventCenterServiceClient,
        context_assembler: DelegatedTaskContextAssembler | None = None,
        checkpointer: Any = None,
        checkpointer_factory: Any = None,
    ) -> None:
        # 决策逻辑复用主控台委托工作流（含 ReAct 规划、观察、审查闭环）。
        self.delegated_task_workflow = delegated_task_workflow
        self.event_center_client = event_center_client
        self.context_assembler = context_assembler or DelegatedTaskContextAssembler()
        # checkpointer 可以是已构建实例，也可以是 async 工厂。工厂形式会在首次 run 的
        # 事件循环内创建 AsyncSqliteSaver，避免 aiosqlite 连接绑定到错误的事件循环。
        self.checkpointer = checkpointer
        self._checkpointer_factory = checkpointer_factory
        self._checkpointer_lock = asyncio.Lock()
        self.graph = self._build_graph(self.checkpointer)

    def _build_graph(self, checkpointer: Any = None):
        """按固定主链路组装委托执行图。"""
        graph = StateGraph(DelegatedExecutionState)
        graph.add_node("ingest_event", self._ingest_event)
        graph.add_node("hydrate_context", self._hydrate_context)
        graph.add_node("reconcile_workflow", self._reconcile_workflow)
        graph.add_node("select_ready_step", self._select_ready_step)
        graph.add_node("react", self._react)
        graph.add_node("review", self._review)
        graph.add_node("persist_transition", self._persist_transition)
        graph.add_node("wait", lambda state: {})
        graph.add_node("end", lambda state: {})
        graph.add_node("skip", lambda state: {"route": "skip"})

        graph.add_edge(START, "ingest_event")
        graph.add_edge("ingest_event", "hydrate_context")
        graph.add_edge("hydrate_context", "reconcile_workflow")
        graph.add_edge("reconcile_workflow", "select_ready_step")
        graph.add_conditional_edges(
            "select_ready_step",
            self._route_after_select,
            {"react": "react", "skip": "skip", "persist_transition": "persist_transition"},
        )
        graph.add_edge("skip", END)
        graph.add_edge("react", "review")
        graph.add_conditional_edges(
            "review",
            self._route_after_review,
            {"react": "react", "persist_transition": "persist_transition"},
        )
        graph.add_conditional_edges(
            "persist_transition",
            self._route_after_persist,
            {"end": "end", "wait": "wait", "react": "react"},
        )
        graph.add_edge("wait", END)
        graph.add_edge("end", END)
        return graph.compile(checkpointer=checkpointer)

    async def run(
        self,
        *,
        event: UnifiedEvent,
        task: dict[str, Any],
        model_profile: Any = None,
        claim_token: str = "",
        finalize: bool = False,
        decision: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """执行一次委托事件主链路，返回最终状态与决策。

        编排层已提前认领事件时传入 claim_token，reconcile 节点不再重复认领；
        独立使用时由 reconcile 节点完成租约抢占。

        finalize 为 SEND 后状态写回模式：编排层已执行真实发送，图只按给定决策
        落库（跳过 react/review），并把 SEND_AND_COMPLETE 当作完成转换持久化。
        """
        workflow_id = str(task.get("workflowId") or task.get("workflow_id") or "").strip()
        task_id = str(task.get("id") or task.get("taskId") or "").strip()
        thread_id = workflow_id or task_id or event.event_id
        if self.checkpointer is None and self._checkpointer_factory is not None:
            # 生产路径：async 工厂在当前事件循环内创建 SQLite 快照，并带 checkpointer 重建图。
            async with self._checkpointer_lock:
                if self.checkpointer is None:
                    self.checkpointer = await self._checkpointer_factory()
                    self.graph = self._build_graph(self.checkpointer)
        state = await self.graph.ainvoke(
            {
                "event": event.model_dump(by_alias=True),
                "task": task,
                "model_profile": model_profile,
                "workflow_id": workflow_id,
                "claim_token": str(claim_token or "").strip(),
                "write_back_actions": [],
                "persisted": False,
                "finalize": bool(finalize),
                "decision": dict(decision or {}),
            },
            config={"configurable": {"thread_id": thread_id}},
        )
        return state

    # ------------------------------------------------------------------ nodes

    async def _ingest_event(self, state: DelegatedExecutionState) -> dict[str, Any]:
        """把当前入站事件写入 L0 current_event，历史查询失败时仍能继续推理。"""
        event = UnifiedEvent.model_validate(state["event"])
        task = state.get("task") or {}
        upsert = getattr(self.event_center_client, "upsert_delegated_task_current_event", None)
        task_id = str(task.get("id") or "").strip()
        if callable(upsert) and task_id:
            try:
                await upsert(event, task_id)
            except Exception as exception:
                logger.warning(
                    "写入 L0 当前事件失败，继续使用内存事件推理：taskId=%s, eventId=%s, errorType=%s",
                    task_id,
                    event.event_id,
                    type(exception).__name__,
                )
        return {}

    async def _hydrate_context(self, state: DelegatedExecutionState) -> dict[str, Any]:
        """按步骤会话范围与起点水位组装一次性可信上下文。"""
        event = UnifiedEvent.model_validate(state["event"])
        task = state.get("task") or {}
        envelope, history, pre_task_history = await self._build_context_parts(event, task)
        return {
            "context_envelope": envelope,
            # 保存组装后的历史行，供 react 节点构造动作输入，避免再次请求历史。
            "_history": history,
            "_pre_task_history": pre_task_history,
        }

    async def _reconcile_workflow(self, state: DelegatedExecutionState) -> dict[str, Any]:
        """认领事件租约并识别重复事件；认领失败时整轮直接退出。

        编排层已提前认领（claim_token 非空）时不再重复认领，只校验状态可推进。
        """
        event = UnifiedEvent.model_validate(state["event"])
        task = state.get("task") or {}
        task_id = str(task.get("id") or "").strip()
        existing_token = str(state.get("claim_token") or "").strip()
        if existing_token:
            return {"reconcile_status": "PROCEED"}
        event_id = canonical_message_identity(state["event"], str(event.text or ""))
        claim = getattr(self.event_center_client, "claim_delegated_task_event", None)
        if not callable(claim):
            # 兼容未提供租约接口的测试替身：视为已认领，但无 token 可提交。
            return {"claim_token": "", "reconcile_status": "PROCEED"}
        if not task_id or not event_id:
            return {"reconcile_status": "CLAIM_UNAVAILABLE", "route": "claim_unavailable"}
        try:
            result = await claim(event, task_id, event_id, 120)
        except Exception as exception:
            logger.warning(
                "抢占委托事件失败，拒绝本轮执行：taskId=%s, eventId=%s, errorType=%s",
                task_id,
                event_id,
                type(exception).__name__,
            )
            return {"reconcile_status": "CLAIM_UNAVAILABLE", "route": "claim_unavailable"}
        if not bool(result.get("claimed")):
            return {"reconcile_status": "CLAIM_UNAVAILABLE", "route": "claim_unavailable"}
        return {
            "claim_token": str(result.get("claimToken") or ""),
            "reconcile_status": "PROCEED",
        }

    @staticmethod
    def _select_ready_step(state: DelegatedExecutionState) -> dict[str, Any]:
        """判断当前事件是否驱动该步骤：属于步骤会话的消息或激活事件才继续。"""
        event = state.get("event") or {}
        task = state.get("task") or {}
        event_type = str(event.get("eventType") or "").lower()
        scope = {
            "platform": str(event.get("platform") or "").strip().lower(),
            "chatType": str(event.get("chatType") or "").strip().lower(),
            "chatId": str(event.get("chatId") or "").strip(),
        }
        task_scope = {
            "platform": str(task.get("platform") or "").strip().lower(),
            "chatType": str(task.get("chatType") or "").strip().lower(),
            "chatId": str(task.get("chatId") or "").strip(),
        }
        scoped = all(task_scope.values())
        if scoped and scope != task_scope:
            return {"route": "skip"}
        if event_type == "message" or "activated" in event_type or "started" in event_type:
            return {"route": "proceed"}
        return {"route": "proceed"}

    async def _react(self, state: DelegatedExecutionState) -> dict[str, Any]:
        """ReAct 决策：用统一上下文构造动作输入，复用委托工作流的决策闭环。"""
        event = UnifiedEvent.model_validate(state["event"])
        task = state.get("task") or {}
        model_profile = state.get("model_profile")
        envelope = state.get("context_envelope") or {}
        history = list(state.get("_history") or [])
        pre_task_history = list(state.get("_pre_task_history") or [])
        action_input = DelegatedTaskActionInput(
            task=task,
            history=history,
            event=state["event"],
            preTaskHistory=pre_task_history,
            historyAccessAllowed=self._history_access_allowed(task),
            contextEnvelope=envelope,
        )
        try:
            decision = await self.delegated_task_workflow.decide_action(action_input, model_profile)
        except Exception as exception:
            logger.warning(
                "委托动作决策失败，本轮安全等待：taskId=%s, eventId=%s, errorType=%s",
                task.get("id"),
                event.event_id,
                type(exception).__name__,
            )
            decision = DelegatedTaskActionDecision(
                action="WAIT",
                reason="决策图不可用，本轮不执行外部副作用",
                progressSummary=str(task.get("progressSummary") or "等待下一次可靠决策"),
                stateJson=str(task.get("stateJson") or "{}"),
                lastEventId=canonical_message_identity(state["event"], str(event.text or "")),
                requestedTool="update_delegated_task",
            )
        return {"decision": decision.model_dump(by_alias=True)}

    async def _review(self, state: DelegatedExecutionState) -> dict[str, Any]:
        """候选回复的外部安全检查：泄露内部定位词时打回重规划。"""
        decision = state.get("decision") or {}
        candidate = str(decision.get("messageInstruction") or "").strip()
        if not candidate or decision.get("action") not in {"SEND_MESSAGE", "SEND_AND_COMPLETE"}:
            return {"transition": {"review": "approved"}}
        guard = getattr(self.delegated_task_workflow, "reply_guard", None)
        task = state.get("task") or {}
        internal_terms = self._internal_terms(task)
        if guard is None or not internal_terms:
            return {"transition": {"review": "approved"}}
        result = guard.validate(candidate, internal_terms)
        if result.allowed:
            return {"transition": {"review": "approved"}}
        logger.warning(
            "候选回复包含内部定位术语，打回重规划：taskId=%s, reason=%s",
            task.get("id"),
            "; ".join(result.reasons),
        )
        return {"transition": {"review": "revise"}}

    async def _persist_transition(self, state: DelegatedExecutionState) -> dict[str, Any]:
        """持久化 WAIT/COMPLETE 转换；SEND 交回编排层执行真实发送。"""
        decision = state.get("decision") or {}
        action = str(decision.get("action") or "WAIT")
        event = UnifiedEvent.model_validate(state["event"])
        task = state.get("task") or {}
        task_id = str(task.get("id") or "").strip()
        workflow_id = str(task.get("workflowId") or "").strip()
        step_key = str(task.get("stepKey") or "").strip()
        claim_token = str(state.get("claim_token") or "").strip()
        stable_event_id = canonical_message_identity(state["event"], str(event.text or ""))
        write_back_actions = list(state.get("write_back_actions") or [])
        finalize = bool(state.get("finalize"))

        # finalize 模式（SEND 后写回）：SEND_AND_COMPLETE 的发送已成功，按完成转换落库。
        if finalize and action == "SEND_AND_COMPLETE":
            action = "COMPLETE_TASK"

        if action in {"SEND_MESSAGE", "SEND_AND_COMPLETE"} and not finalize:
            # 真实发送需要 SocialAgent 与写回层，由编排层在图外执行；租约保持占用。
            write_back_actions.append("delegated_task_action:" + action.lower())
            return {"route": "send", "transition": {"kind": "send"}, "write_back_actions": write_back_actions}

        try:
            if action == "COMPLETE_TASK" and workflow_id and step_key:
                produced_facts = self._workflow_produced_facts(task, decision, event)
                artifacts = [
                    {
                        "type": str(key).strip().upper(),
                        "name": key,
                        "value": value,
                        "sourceEventId": stable_event_id,
                    }
                    for key, value in produced_facts.items()
                ]
                await self.event_center_client.complete_delegated_workflow_step(
                    event,
                    workflow_id,
                    step_key,
                    produced_facts=produced_facts,
                    result_summary=str(decision.get("completionReport") or decision.get("progressSummary") or "步骤已完成。"),
                    result={
                        "taskId": task_id,
                        "action": action,
                        "evidence": list(decision.get("evidence") or []),
                        "state": self._parse_json_object(decision.get("stateJson")),
                        "completionReport": str(decision.get("completionReport") or ""),
                    },
                    artifacts=artifacts,
                    source_event_id=stable_event_id,
                )
                write_back_actions.append("delegated_task_action:" + action.lower())
                write_back_actions.append("delegated_workflow_step_completed:" + step_key)
            else:
                await self.event_center_client.update_delegated_task_runtime(
                    event,
                    task_id,
                    status="COMPLETED" if action == "COMPLETE_TASK" else "ACTIVE",
                    progress_summary=str(decision.get("progressSummary") or "任务仍在进行"),
                    state_json=str(decision.get("stateJson") or "{}"),
                    last_event_id=stable_event_id,
                    completion_report=str(decision.get("completionReport") or ""),
                )
                write_back_actions.append("delegated_task_action:" + action.lower())
                write_back_actions.append(
                    "delegated_task_runtime_updated:" + ("completed" if action == "COMPLETE_TASK" else "active")
                )
            if claim_token:
                complete_claim = getattr(self.event_center_client, "complete_delegated_task_event", None)
                if callable(complete_claim):
                    await complete_claim(event, task_id, stable_event_id, claim_token)
            persisted_status = "COMPLETED" if action == "COMPLETE_TASK" else "ACTIVE"
            return {
                "route": "done",
                "persisted": True,
                "transition": {"kind": "done", "status": persisted_status},
                "write_back_actions": write_back_actions,
            }
        except DelegatedWorkflowFactsMissingError as exception:
            # 模型判断完成但没有给齐父工作流声明事实：保留任务并记录缺口，等待后续消息。
            logger.warning(
                "父步骤声明事实不足，保持等待：taskId=%s, missing=%s",
                task_id,
                list(exception.missing_facts),
            )
            waiting_state = self._parse_json_object(decision.get("stateJson"))
            waiting_state["workflowCompletionPending"] = {
                "missingFacts": list(exception.missing_facts),
                "eventId": stable_event_id,
                "reason": str(exception),
            }
            await self.event_center_client.update_delegated_task_runtime(
                event,
                task_id,
                status="ACTIVE",
                progress_summary="等待补充父工作流事实：" + "、".join(exception.missing_facts),
                state_json=json.dumps(waiting_state, ensure_ascii=False),
                last_event_id=stable_event_id,
                completion_report="",
            )
            if claim_token:
                complete_claim = getattr(self.event_center_client, "complete_delegated_task_event", None)
                if callable(complete_claim):
                    await complete_claim(event, task_id, stable_event_id, claim_token)
            return {
                "route": "wait",
                "persisted": True,
                "transition": {"kind": "facts_wait", "status": "ACTIVE", "missingFacts": list(exception.missing_facts)},
                "write_back_actions": write_back_actions,
            }
        except Exception as exception:
            logger.error(
                "委托转换持久化失败，释放租约等待重试：taskId=%s, eventId=%s, errorType=%s",
                task_id,
                event.event_id,
                type(exception).__name__,
            )
            if claim_token:
                release = getattr(self.event_center_client, "release_delegated_task_event", None)
                if callable(release):
                    try:
                        await release(event, task_id, stable_event_id, claim_token)
                    except Exception:
                        logger.exception("释放委托事件租约失败：taskId=%s", task_id)
            return {"route": "retry", "persisted": False, "transition": {"kind": "retry"}}

    # --------------------------------------------------------------- routing

    @staticmethod
    def _route_after_select(state: DelegatedExecutionState) -> str:
        # finalize 模式跳过 react/review，直接用给定决策落库。
        if state.get("finalize"):
            return "persist_transition"
        return "skip" if state.get("route") == "skip" else "react"

    @staticmethod
    def _route_after_review(state: DelegatedExecutionState) -> str:
        transition = state.get("transition") or {}
        return "react" if transition.get("review") == "revise" else "persist_transition"

    @staticmethod
    def _route_after_persist(state: DelegatedExecutionState) -> str:
        route = state.get("route") or "done"
        if route == "wait":
            return "wait"
        # retry / send / done 都是终态：retry 由编排层释放租约等待重投，send 由编排层执行真实发送。
        return "end"

    # -------------------------------------------------------------- helpers

    async def _build_context_parts(
        self,
        event: UnifiedEvent,
        task: dict[str, Any],
    ) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
        """读取步骤历史（L1）、有限背景（L2）并组装统一上下文包。"""
        task_state = self._task_state(task)
        platform, chat_type, chat_id = self._step_scope(task)
        if not all((platform, chat_type, chat_id)):
            platform = str(event.platform or "").strip().lower()
            chat_type = str(event.chat_type or "").strip().lower()
            chat_id = str(event.chat_id or "").strip()
        started_at, _start_event_id = self._step_watermark(task, task_state)
        query_after = started_at or str(task_state.get("taskCreatedAt") or task.get("createdAt") or "").strip() or None

        history: list[dict[str, Any]] = []
        try:
            rows = await self.event_center_client.list_conversation_messages(
                chat_id,
                platform=platform or None,
                chat_type=chat_type or None,
                limit=500,
                user_id=EventCenterServiceClient.resolve_event_user_id(event),
                after=query_after or None,
            )
            history = self._filter_history(rows, event, task)
        except Exception as exception:
            logger.warning(
                "委托历史查询失败，改用当前事件继续推理。taskId=%s | %s",
                task.get("id"),
                self._history_diagnostic(exception, (platform, chat_type, chat_id), query_after, None, 500),
            )
            current_event = await self._load_current_event(event, task)
            if current_event:
                history = self._merge_current_event(history, current_event)

        pre_task_history: list[dict[str, Any]] = []
        if self._history_access_allowed(task) and query_after:
            try:
                rows = await self.event_center_client.list_conversation_messages(
                    chat_id,
                    platform=platform or None,
                    chat_type=chat_type or None,
                    limit=30,
                    user_id=EventCenterServiceClient.resolve_event_user_id(event),
                    before=query_after,
                )
                pre_task_history = self._filter_history(rows, event, task)
            except Exception as exception:
                logger.warning(
                    "读取任务前背景失败，继续使用任务内记忆。taskId=%s | %s",
                    task.get("id"),
                    self._history_diagnostic(exception, (platform, chat_type, chat_id), None, query_after, 30),
                )

        envelope = self.context_assembler.assemble(
            event=event.model_dump(by_alias=True),
            task=task,
            task_history=history,
            pre_task_history=pre_task_history or None,
        )
        return envelope, history, pre_task_history

    async def _load_current_event(self, event: UnifiedEvent, task: dict[str, Any]) -> dict[str, Any] | None:
        reader = getattr(self.event_center_client, "get_delegated_task_current_event", None)
        task_id = str(task.get("id") or "").strip()
        if not callable(reader) or not task_id:
            return None
        try:
            stored = await reader(event, task_id)
        except Exception as exception:
            logger.warning("读取 L0 当前事件失败：taskId=%s, errorType=%s", task_id, type(exception).__name__)
            return None
        if not isinstance(stored, dict) or not stored.get("eventId"):
            return None
        payload = stored.get("payload")
        if isinstance(payload, dict):
            return payload
        payload_json = stored.get("payloadJson")
        if isinstance(payload_json, str) and payload_json.strip():
            parsed = self._parse_json_object(payload_json)
            if parsed:
                return parsed
        return {
            "eventId": stored.get("eventId"),
            "eventType": stored.get("eventType") or "message",
            "text": stored.get("text") or "",
            "sentAt": stored.get("occurredAt"),
        }

    @classmethod
    def _merge_current_event(cls, history: list[dict[str, Any]], current_event: dict[str, Any]) -> list[dict[str, Any]]:
        if not isinstance(current_event, dict) or not current_event.get("eventId"):
            return history
        event_id = str(current_event.get("eventId") or "").strip()
        for row in history:
            row_id = str(row.get("eventId") or row.get("event_id") or row.get("platformMessageId") or "")
            if row_id == event_id:
                return history
        return [
            *history,
            {
                "eventId": event_id,
                "platformMessageId": str(current_event.get("platformMessageId") or "").strip(),
                "clientMessageId": str(current_event.get("clientMessageId") or "").strip(),
                "at": str(
                    current_event.get("sentAt")
                    or current_event.get("timestamp")
                    or current_event.get("occurredAt")
                    or current_event.get("receivedAt")
                    or ""
                ).strip(),
                "text": " ".join(str(current_event.get("text") or current_event.get("content") or "").split()),
                "eventType": str(current_event.get("eventType") or "message").lower(),
                "direction": str(current_event.get("direction") or "").upper(),
                "actorType": str(current_event.get("actorType") or "").upper(),
                "messageOrigin": str(current_event.get("messageOrigin") or "").upper(),
                "platform": str(current_event.get("platform") or "").strip(),
                "chatType": str(current_event.get("chatType") or "").strip(),
                "chatId": str(current_event.get("chatId") or "").strip(),
                "sender": current_event.get("sender") if isinstance(current_event.get("sender"), dict) else {},
            },
        ]

    @classmethod
    def _history_diagnostic(
        cls,
        exception: Exception,
        scope: tuple[str, str, str],
        after: str | None,
        before: str | None,
        limit: int,
    ) -> str:
        http_status = ""
        response_body = ""
        if hasattr(exception, "response") and exception.response is not None:
            response = exception.response
            http_status = str(getattr(response, "status_code", ""))
            response_body = " ".join(str(getattr(response, "text", "") or "").split())[:500]
        return (
            "请求范围=platform=" + str(scope[0] or "-")
            + ",chatType=" + str(scope[1] or "-")
            + ",chatId=" + str(scope[2] or "-")
            + ",limit=" + str(limit)
            + ",after=" + str(after or "-")
            + ",before=" + str(before or "-")
            + " | httpStatus=" + (http_status or "-")
            + " | responseBody=" + (response_body or "-")
            + " | errorType=" + type(exception).__name__
            + " | message=" + " ".join(str(exception).split())[:300]
        )

    @classmethod
    def _filter_history(
        cls,
        rows: list[dict[str, Any]] | None,
        event: UnifiedEvent,
        task: dict[str, Any],
    ) -> list[dict[str, Any]]:
        expected = cls._row_scope(event.model_dump(by_alias=True))
        task_scope = cls._row_scope(task)
        filtered: list[dict[str, Any]] = []
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            row_scope = cls._row_scope(row)
            if all(row_scope):
                if row_scope != expected:
                    continue
            elif row_scope != ("", "", "") or task_scope != expected:
                continue
            filtered.append(row)
        return filtered

    @staticmethod
    def _row_scope(row: dict[str, Any]) -> tuple[str, str, str]:
        payload = row.get("rawPayload") if isinstance(row.get("rawPayload"), dict) else {}
        platform = str(row.get("platform") or payload.get("platform") or "").strip().lower()
        chat_type = str(row.get("chatType") or row.get("chat_type") or payload.get("chatType") or "").strip().lower()
        chat_id = str(
            row.get("chatId")
            or row.get("chat_id")
            or payload.get("chatId")
            or payload.get("group_id")
            or payload.get("user_id")
            or ""
        ).strip()
        return platform, chat_type, chat_id

    @classmethod
    def _step_scope(cls, task: dict[str, Any]) -> tuple[str, str, str]:
        raw = task.get("conversationScopeJson") or task.get("conversation_scope_json") or task.get("conversationScope")
        if isinstance(raw, str) and raw.strip():
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict):
                platform = str(parsed.get("platform") or "").strip()
                chat_type = str(parsed.get("chatType") or parsed.get("chat_type") or "").strip().lower()
                chat_id = str(parsed.get("chatId") or parsed.get("chat_id") or "").strip()
                if platform and chat_type and chat_id:
                    return platform, chat_type, chat_id
        return cls._row_scope(task)

    @staticmethod
    def _step_watermark(task: dict[str, Any], state: dict[str, Any]) -> tuple[str, str]:
        started_at = str(
            task.get("startedAt")
            or task.get("started_at")
            or state.get("taskStartedAt")
            or state.get("taskCreatedAt")
            or task.get("createdAt")
            or ""
        ).strip()
        start_event_id = str(task.get("startEventId") or task.get("start_event_id") or "").strip()
        return started_at, start_event_id

    @staticmethod
    def _task_state(task: dict[str, Any]) -> dict[str, Any]:
        raw_state = task.get("stateJson")
        if isinstance(raw_state, dict):
            return raw_state
        if not isinstance(raw_state, str) or not raw_state.strip():
            return {}
        try:
            parsed = json.loads(raw_state)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @classmethod
    def _history_access_allowed(cls, task: dict[str, Any]) -> bool:
        state = cls._task_state(task)
        value = task.get("historyAccessAllowed", task.get("allowPreTaskHistory", state.get("historyAccessAllowed", True)))
        return str(value).strip().lower() not in {"false", "0", "no", "off"}

    @staticmethod
    def _parse_json_object(value: object) -> dict[str, Any]:
        if isinstance(value, dict):
            return dict(value)
        if not isinstance(value, str) or not value.strip():
            return {}
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def _workflow_produced_facts(
        self,
        task: dict[str, Any],
        decision: dict[str, Any],
        event: UnifiedEvent,
    ) -> dict[str, Any]:
        """提取父步骤声明事实，并从单一事实型联系人回复中恢复模型漏填的值。"""
        declared = task.get("producesFacts")
        fact_keys = [str(item).strip() for item in declared if str(item).strip()] if isinstance(declared, list) else []
        facts: dict[str, Any] = {}
        missing: list[str] = []
        state = self._parse_json_object(decision.get("stateJson"))
        # 依次从 producedFacts 映射、knownFacts 列表与状态顶层恢复声明事实。
        produced_map = state.get("producedFacts") if isinstance(state.get("producedFacts"), dict) else {}
        for key in fact_keys:
            value = state.get(key)
            if value is None and key in produced_map:
                value = produced_map[key]
            if value is not None and str(value).strip():
                facts[key] = value
                continue
            found = False
            for fact in state.get("knownFacts") or []:
                if isinstance(fact, dict) and str(fact.get("name") or "") == key and fact.get("value") is not None:
                    facts[key] = fact["value"]
                    found = True
                    break
            if not found:
                missing.append(key)
        if missing and len(fact_keys) == 1 and self._is_peer_inbound(event):
            reply_text = " ".join(str(event.text or "").split()).strip()
            if reply_text:
                facts[fact_keys[0]] = reply_text
                missing.clear()
        if missing:
            raise DelegatedWorkflowFactsMissingError(missing)
        return facts

    @staticmethod
    def _is_peer_inbound(event: UnifiedEvent) -> bool:
        event_type = str(event.event_type or "").lower()
        direction = str(event.direction or "").upper()
        actor = str(event.actor_type or "").upper()
        raw_payload = event.raw_payload or {}
        origin = str(raw_payload.get("messageOrigin") or "").upper()
        sender_id = str(event.sender.id if event.sender else "").strip()
        self_id = str(event.self_id or "").strip()
        chat_id = str(event.chat_id or "").strip()
        chat_type = str(event.chat_type or "").strip().lower()
        if not (event.text or "").strip() or event_type != "message":
            return False
        if is_runtime_generated_message(event.model_dump(by_alias=True)):
            return False
        if direction == "OUTBOUND" or actor in {"AGENT", "SYSTEM", "SELF"}:
            return False
        if origin in {"INTERNAL", "AGENT", "AGENT_AUTO", "AGENT_CONFIRMED", "USER_MANUAL"}:
            return False
        if sender_id and self_id and sender_id == self_id:
            return False
        if direction == "INBOUND" or actor in {"CONTACT", "PEER", "REMOTE"}:
            return True
        if origin in {"EXTERNAL", "PLATFORM"}:
            return True
        return bool(
            chat_type == "private"
            and sender_id
            and self_id
            and chat_id
            and sender_id == chat_id
            and sender_id != self_id
        )

    @staticmethod
    def _internal_terms(task: dict[str, Any]) -> tuple[str, ...]:
        values = (task.get("targetName"), task.get("target_name"), task.get("targetQuery"), task.get("target_query"))
        return tuple(text for value in values if len(text := " ".join(str(value or "").split())) >= 2)


async def build_execution_sqlite_checkpointer(db_path: str | None = None):
    """构建跨重启可恢复的异步 SQLite Checkpointer。

    Checkpoint 只保存 LangGraph 运行快照（以 workflowId 为 thread key），
    不能代替 Event Center 的聊天事件与业务状态。主链路图使用 async 调用，
    因此必须使用 AsyncSqliteSaver；aiosqlite 连接绑定创建它的事件循环，
    所以本函数只能由事件循环内的调用方 await（首次 run 时懒加载）。
    """
    import os

    import aiosqlite
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    if not db_path:
        runtime_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".runtime")
        db_path = os.getenv("LANGGRAPH_CHECKPOINT_DB") or os.path.join(runtime_dir, "langgraph-checkpoints.sqlite")
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    connection = await aiosqlite.connect(db_path)
    saver = AsyncSqliteSaver(connection)
    await saver.setup()
    return saver


def build_delegated_execution_graph(
    *,
    delegated_task_workflow: Any,
    event_center_client: EventCenterServiceClient,
    checkpointer: Any | None = None,
) -> DelegatedExecutionGraph:
    """构造固定主链路执行图；未提供 checkpointer 时默认懒加载异步 SQLite 快照。"""
    if checkpointer is None:
        return DelegatedExecutionGraph(
            delegated_task_workflow=delegated_task_workflow,
            event_center_client=event_center_client,
            checkpointer_factory=build_execution_sqlite_checkpointer,
        )
    return DelegatedExecutionGraph(
        delegated_task_workflow=delegated_task_workflow,
        event_center_client=event_center_client,
        checkpointer=checkpointer,
    )
