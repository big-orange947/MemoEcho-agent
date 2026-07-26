from __future__ import annotations

import json
import os
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any, TypedDict
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from langgraph.graph import END, START, StateGraph

from app.clients.event_center_service import EventCenterServiceClient
from app.clients.llm_service import LlmServiceClient
from app.services.react_context import CandidateReplyGuard, MasterTaskContextBuilder
from app.schemas.delegated_tasks import (
    ConversationCandidate,
    DelegatedTaskActionDecision,
    DelegatedTaskActionInput,
    DelegatedTaskCompileRequest,
    DelegatedTaskCompileResponse,
    DelegatedTaskRuntimeDecision,
    DelegatedTaskRuntimeInput,
)
from app.tools.langchain_delegated_task_tools import delegated_task_action_tools


class CompileState(TypedDict, total=False):
    """保存自然语言命令编译图在各节点之间传递的中间状态。"""

    request: DelegatedTaskCompileRequest
    model_profile: Any
    normalized_command: str
    target_query: str
    target_chat_type: str
    intent: dict[str, Any]
    target: ConversationCandidate | None
    result: DelegatedTaskCompileResponse


class RuntimeState(TypedDict, total=False):
    """保存委托运行图的可信时间线、完成判断和最终状态。"""

    runtime_input: DelegatedTaskRuntimeInput
    model_profile: Any
    timeline: list[dict[str, Any]]
    previous_state: dict[str, Any]
    evaluation: dict[str, Any]
    result: DelegatedTaskRuntimeDecision


class ActionState(TypedDict, total=False):
    """保存委托任务在回复生成前进行动作选择时使用的可信状态。"""

    action_input: DelegatedTaskActionInput
    runtime_input: DelegatedTaskRuntimeInput
    model_profile: Any
    timeline: list[dict[str, Any]]
    previous_state: dict[str, Any]
    evaluation: dict[str, Any]
    model_context: dict[str, Any]
    react_iteration: int
    review_iteration: int
    review_feedback: str
    review_decision: str
    react_trace: list[dict[str, Any]]
    selected_action: dict[str, Any]
    # 图内观察工具的结果必须进入下一轮规划，避免只把读取意图交回 Java 后丢失上下文。
    pre_task_history: list[dict[str, Any]]
    tool_observations: list[dict[str, Any]]
    result: DelegatedTaskActionDecision


class DelegatedTaskWorkflow:
    """用两张 LangGraph 图管理任务编译和跨重启的会话目标执行。"""

    _INTENT_PATTERN = re.compile(r"(帮我|替我|代我).*(回|回复|聊|约|问|联系|告诉|协商|通知|确认)")
    _DEADLINE_PATTERN = re.compile(
        r"(今天|明天|后天|本周|下周|今晚|明早|明晚|\d{1,2}月\d{1,2}日|"
        r"周[一二三四五六日天]|星期[一二三四五六日天])[^，。；;]*"
    )
    def __init__(
        self,
        llm_client: LlmServiceClient,
        event_center_client: EventCenterServiceClient | None = None,
    ) -> None:
        # 这个构造函数的作用是保存模型客户端，并预编译任务编译、动作选择和运行更新三张状态图。
        self.llm_client = llm_client
        # 历史读取属于只读观察，在 LangGraph 内执行后回灌模型上下文，不通过 Java 形成悬空动作。
        self.event_center_client = event_center_client or EventCenterServiceClient()
        # 主控台任务只向模型暴露经过筛选的工作上下文，内部定位和执行字段保留在编排层。
        self.context_builder = MasterTaskContextBuilder()
        # 只有主控台委托图持有这些 LangChain 工具，会话设定集不经过该图，
        # 因而模型无法在设定集路径自主调用结束任务工具。
        self.action_tools = delegated_task_action_tools()
        # 所有模型动作都必须从这里取得已声明的 LangChain @tool，禁止另行维护工具别名或手写参数协议。
        self.action_tools_by_name = {tool.name: tool for tool in self.action_tools}
        # 候选文本在真正交给发送层前还会做一次动态泄漏检查，避免模型把内部会话定位词写进聊天内容。
        self.reply_guard = CandidateReplyGuard()
        self.compile_graph = self._build_compile_graph()
        self.action_graph = self._build_action_graph()
        self.runtime_graph = self._build_runtime_graph()

    def _build_compile_graph(self):
        """构建“归一化命令 -> 理解 -> 白名单解析 -> 契约输出”的任务编译图。"""
        graph = StateGraph(CompileState)
        graph.add_node("normalize", self._normalize_command)
        graph.add_node("understand", self._understand_command)
        graph.add_node("resolve_target", self._resolve_authorized_target)
        graph.add_node("compile_contract", self._compile_contract)
        graph.add_edge(START, "normalize")
        graph.add_edge("normalize", "understand")
        graph.add_edge("understand", "resolve_target")
        graph.add_edge("resolve_target", "compile_contract")
        graph.add_edge("compile_contract", END)
        return graph.compile()

    def _build_runtime_graph(self):
        """构建“可信时间线 -> 完成判断 -> 持久化状态”的任务运行图。"""
        graph = StateGraph(RuntimeState)
        graph.add_node("build_timeline", self._build_timeline)
        graph.add_node("evaluate", self._evaluate_runtime)
        graph.add_node("finalize", self._finalize_runtime)
        graph.add_edge(START, "build_timeline")
        graph.add_edge("build_timeline", "evaluate")
        graph.add_edge("evaluate", "finalize")
        graph.add_edge("finalize", END)
        return graph.compile()

    def _build_action_graph(self):
        """构建主控台专用的 ReAct 动作图。

        这条图只产出受控的对外工具意图：真实发送和结束任务仍由 Java 服务执行；
        任务前历史则作为图内观察工具直接读取，并回灌给下一轮规划。
        候选消息必须先经过情景审查；审查打回时回到规划节点，并携带通用反馈重新思考。
        """
        graph = StateGraph(ActionState)
        graph.add_node("build_timeline", self._build_action_timeline)
        graph.add_node("plan_react", self._plan_react_action)
        graph.add_node("observe_tool", self._observe_react_tool)
        graph.add_node("review_candidate", self._review_react_candidate)
        graph.add_node("select_action", self._select_runtime_action)
        graph.add_node("finalize_action", self._finalize_action)
        graph.add_edge(START, "build_timeline")
        graph.add_edge("build_timeline", "plan_react")
        graph.add_conditional_edges(
            "plan_react",
            self._route_after_react_plan,
            {
                "observe_tool": "observe_tool",
                "review_candidate": "review_candidate",
                "select_action": "select_action",
            },
        )
        graph.add_edge("observe_tool", "plan_react")
        graph.add_conditional_edges(
            "review_candidate",
            self._route_after_react_review,
            {"plan_react": "plan_react", "select_action": "select_action"},
        )
        graph.add_edge("select_action", "finalize_action")
        graph.add_edge("finalize_action", END)
        return graph.compile()

    async def compile_task(
        self,
        request: DelegatedTaskCompileRequest,
        model_profile: Any = None,
    ) -> DelegatedTaskCompileResponse:
        """执行任务编译图，并返回可直接反序列化到 Java record 的结果。"""
        state = await self.compile_graph.ainvoke({"request": request, "model_profile": model_profile})
        return state["result"]

    async def evaluate_runtime(
        self,
        runtime_input: DelegatedTaskRuntimeInput,
        model_profile: Any = None,
    ) -> DelegatedTaskRuntimeDecision:
        """执行运行状态图；失败时由调用层保留旧状态，不影响已生成的聊天回复。"""
        state = await self.runtime_graph.ainvoke(
            {"runtime_input": runtime_input, "model_profile": model_profile}
        )
        return state["result"]

    async def decide_action(
        self,
        action_input: DelegatedTaskActionInput,
        model_profile: Any = None,
    ) -> DelegatedTaskActionDecision:
        """在生成聊天草稿前决定本轮应发送、等待还是结束任务。"""
        state = await self.action_graph.ainvoke(
            {"action_input": action_input, "model_profile": model_profile}
        )
        return state["result"]

    async def resolve_workspace_command_targets(
        self,
        command: str,
        candidates: list[ConversationCandidate],
        model_profile: Any = None,
    ) -> list[ConversationCandidate]:
        """解析主控台自然语言命令要作用到哪些授权会话。

        主控台命令不能再由 Java 或正则提前拆联系人，否则容易把“km预约”这类动作词
        拼进联系人名称。这里先让模型在授权候选列表中选择目标；模型不可用或返回越界结果时，
        再用保守的本地显式提及匹配兜底。
        """
        if not candidates:
            return []

        if not self.llm_client.is_enabled(model_profile):
            return self._fallback_resolve_workspace_targets(command, candidates)

        system_prompt = (
            "你是 Memo Echo 主控台命令路由器，只输出 JSON，不要解释。"
            "你的任务是从 authorizedConversations 中选择用户命令明确提到的目标会话，支持多选。"
            "只能返回候选里真实存在的 chatId 和 chatType，禁止创造联系人、禁止把任务动作词当联系人。"
            "如果用户明确说群聊、群里、群内、这个群，才优先选择群聊；否则提到人名时优先私聊。"
            "输出格式：{\"targets\":[{\"chatId\":\"...\",\"chatType\":\"private|group\",\"reason\":\"...\"}],\"reason\":\"...\"}。"
            "无法确定目标时 targets 返回空数组。"
        )
        user_message = json.dumps(
            {
                "command": command,
                "authorizedConversations": [self._candidate_payload(candidate) for candidate in candidates],
            },
            ensure_ascii=False,
        )
        try:
            raw = await self.llm_client.generate_reply(
                system_prompt,
                user_message,
                temperature=0.05,
                model_profile=model_profile,
            )
            parsed = self._parse_json_object(raw)
            selected = self._materialize_router_targets(parsed.get("targets") or [], candidates)
            if selected:
                return selected
        except Exception:
            pass
        return self._fallback_resolve_workspace_targets(command, candidates)

    def _candidate_payload(self, candidate: ConversationCandidate) -> dict[str, Any]:
        """把授权会话压缩成模型路由可读的候选信息。

        这里只暴露选择目标所需的最小字段，不把完整聊天内容交给路由器，避免命令路由阶段
        误把上下文当成需要回复的消息。
        """
        return {
            "platform": candidate.platform,
            "chatType": self._normalize_chat_type(candidate.chat_type),
            "chatId": candidate.chat_id,
            "chatName": candidate.chat_name,
            "lastSenderName": candidate.last_sender_name,
            "lastMessage": candidate.last_message,
            "lastMessageTime": candidate.last_message_time,
            "autoReplyEnabled": candidate.auto_reply_enabled,
            "summaryEnabled": candidate.summary_enabled,
        }

    def _materialize_router_targets(
        self,
        targets: Any,
        candidates: list[ConversationCandidate],
    ) -> list[ConversationCandidate]:
        """把模型返回的目标映射回本地白名单候选，过滤所有越权或不存在的会话。"""
        index: dict[tuple[str, str], ConversationCandidate] = {
            (self._normalize_chat_type(candidate.chat_type), candidate.chat_id): candidate
            for candidate in candidates
            if candidate.chat_id
        }
        result: list[ConversationCandidate] = []
        seen: set[tuple[str, str]] = set()
        for raw in targets if isinstance(targets, list) else []:
            if isinstance(raw, str):
                chat_id = raw.strip()
                chat_type = ""
            elif isinstance(raw, dict):
                chat_id = str(raw.get("chatId") or raw.get("chat_id") or "").strip()
                chat_type = self._normalize_chat_type(str(raw.get("chatType") or raw.get("chat_type") or ""))
            else:
                continue
            if not chat_id:
                continue
            matched: ConversationCandidate | None = None
            if chat_type:
                matched = index.get((chat_type, chat_id))
            else:
                for candidate in candidates:
                    if candidate.chat_id == chat_id:
                        matched = candidate
                        break
            if not matched:
                continue
            key = (self._normalize_chat_type(matched.chat_type), matched.chat_id)
            if key in seen:
                continue
            seen.add(key)
            result.append(matched)
        return result

    def _fallback_resolve_workspace_targets(
        self,
        command: str,
        candidates: list[ConversationCandidate],
    ) -> list[ConversationCandidate]:
        """模型不可用时使用保守显式提及匹配，支持同一句命令提到多个联系人。

        该兜底只处理命令中明确出现的候选昵称、备注或 QQ 号；没有命中时返回空列表，
        交由后续任务编译给出“需要选择联系人”的状态。
        """
        normalized_command = self._normalize_contact_token(command)
        expected_chat_type = self._infer_target_chat_type(command)
        scored: list[tuple[int, int, ConversationCandidate]] = []
        for candidate in candidates:
            best_position: int | None = None
            best_length = 0
            for alias in self._candidate_aliases(candidate):
                position = normalized_command.find(alias)
                if position < 0:
                    continue
                if best_position is None or position < best_position or len(alias) > best_length:
                    best_position = position
                    best_length = len(alias)
            if best_position is None:
                continue
            chat_type = self._normalize_chat_type(candidate.chat_type)
            type_penalty = 0 if chat_type == expected_chat_type else 1000
            # 没有明确群聊措辞时，同名命中优先私聊，避免“km、小号、哈吉仙”群误入目标。
            if expected_chat_type == "private" and chat_type == "group":
                type_penalty = 500
            scored.append((best_position, type_penalty - best_length, candidate))

        scored.sort(key=lambda item: (item[0], item[1], item[2].chat_name or item[2].chat_id))
        # 明确识别到私聊/群聊意图时，只保留对应类型的候选，避免“群里”命令同时拆出群成员私聊。
        typed_scored = [
            item for item in scored if self._normalize_chat_type(item[2].chat_type) == expected_chat_type
        ]
        selected_scored = typed_scored or scored
        result: list[ConversationCandidate] = []
        seen: set[tuple[str, str, str]] = set()
        for _, _, candidate in selected_scored:
            key = (candidate.platform, self._normalize_chat_type(candidate.chat_type), candidate.chat_id)
            if key in seen:
                continue
            seen.add(key)
            result.append(candidate)
        return result

    def _candidate_aliases(self, candidate: ConversationCandidate) -> set[str]:
        """整理候选会话可被主控台命令命中的别名。"""
        aliases: set[str] = set()
        for raw_value in (candidate.chat_name, candidate.last_sender_name, candidate.chat_id):
            alias = self._normalize_contact_token(raw_value)
            if alias and (len(alias) >= 2 or alias.isdigit()):
                aliases.add(alias)
        return aliases

    @staticmethod
    def _normalize_contact_token(value: str | None) -> str:
        """把昵称、备注和命令文本归一化，便于普通输入命中特殊昵称。"""
        if not value:
            return ""
        ignored_chars = set(" \t\r\n，,。.!！?？、:：;；@")
        normalized = unicodedata.normalize("NFKC", str(value).strip())
        return "".join(char.casefold() for char in normalized if char not in ignored_chars)

    def _normalize_command(self, state: CompileState) -> dict[str, Any]:
        """只清理命令文本，不在编译图里猜联系人。

        目标会话必须由主控台 Router 从授权候选中预先选好。编译图只负责把单个候选会话
        编译成任务契约，避免再次用正则把动作词拼进联系人名称。
        """
        command = " ".join(state["request"].command.split()).strip()
        return {
            "normalized_command": command,
            "target_query": "",
            "target_chat_type": self._infer_target_chat_type(command),
        }

    async def _understand_command(self, state: CompileState) -> dict[str, Any]:
        """优先让模型提取目标和成功条件，模型不可用时回退到保守规则。"""
        command = state["normalized_command"]
        fallback = self._fallback_intent(command, state.get("target_query", ""))
        model_profile = state.get("model_profile")
        if not self.llm_client.is_enabled(model_profile):
            return {"intent": fallback}

        candidates = [
            {
                "chatType": item.chat_type,
                "chatId": item.chat_id,
                "chatName": item.chat_name,
            }
            for item in state["request"].conversations
        ]
        system_prompt = (
            "你是委托任务编译器，只输出 JSON。不要执行任务。"
            "从用户命令中提取 taskType、targetQuery、targetChatType、objective、successCriteria、deadlineText、"
            "resolvedTimeText。resolvedTimeText 必须把命令里的今天、明天、后天等相对时间，"
            "严格按提供的 commandCreatedAt 和 timezone 转换为包含绝对日期的自然语言；没有时间要求时留空。"
            "targetQuery 只能引用候选名称，不得创建联系人。无法判断时保留空字符串。"
        )
        command_created_at = self._now().isoformat()
        user_message = json.dumps(
            {
                "command": command,
                "commandCreatedAt": command_created_at,
                "timezone": self._timezone_name(),
                "authorizedConversations": candidates,
            },
            ensure_ascii=False,
        )
        try:
            raw = await self.llm_client.generate_reply(
                system_prompt,
                user_message,
                temperature=0.1,
                model_profile=model_profile,
            )
            parsed = self._parse_json_object(raw)
            if parsed:
                fallback.update({key: value for key, value in parsed.items() if value not in (None, "")})
        except Exception:
            pass
        if not str(fallback.get("targetChatType") or "").strip():
            fallback["targetChatType"] = state.get("target_chat_type", "private")
        return {
            "intent": fallback,
            "target_query": str(fallback.get("targetQuery") or ""),
            "target_chat_type": str(fallback.get("targetChatType") or "private"),
        }

    def _resolve_authorized_target(self, state: CompileState) -> dict[str, Any]:
        """只在 Java 提供的会话白名单中解析唯一目标，禁止模型绑定任意 QQ 号。"""
        request = state["request"]
        if request.target_resolved_by_router and len(request.conversations) == 1:
            target = request.conversations[0]
            return {
                "target": target,
                "target_query": target.chat_name or target.last_sender_name or target.chat_id,
                "target_chat_type": self._normalize_chat_type(target.chat_type),
            }
        command = self._normalize_lookup_text(state["normalized_command"])
        query = self._normalize_lookup_text(
            state.get("target_query") or state.get("intent", {}).get("targetQuery") or ""
        )
        expected_chat_type = self._normalize_chat_type(
            str(state.get("target_chat_type") or self._infer_target_chat_type(command))
        )
        scored: list[tuple[int, ConversationCandidate]] = []
        for candidate in request.conversations:
            if self._normalize_chat_type(candidate.chat_type) != expected_chat_type:
                continue
            # last_sender_name 只代表最后发言人，不能证明该会话就是目标联系人。
            names = {candidate.chat_name.strip(), candidate.chat_id.strip()}
            names.discard("")
            score = 0
            for name in names:
                normalized_name = self._normalize_lookup_text(name)
                if query and query == normalized_name:
                    score = max(score, 200)
                elif query and (query in normalized_name or normalized_name in query):
                    # 包含匹配越接近用户输入越可靠，不能再让长群名获得更高分。
                    score = max(score, 100 - abs(len(normalized_name) - len(query)))
                elif normalized_name in command:
                    score = max(score, 50 - min(len(normalized_name), 40))
            if score:
                scored.append((score, candidate))
        scored.sort(key=lambda item: item[0], reverse=True)
        if not scored or (len(scored) > 1 and scored[0][0] == scored[1][0]):
            return {"target": None}
        return {"target": scored[0][1], "target_query": scored[0][1].chat_name or query}

    @staticmethod
    def _normalize_lookup_text(value: Any) -> str:
        """统一联系人检索文本，使 `㎞` 等兼容字符可以被普通键盘输入的 `km` 命中。"""
        normalized = unicodedata.normalize("NFKC", str(value or ""))
        return " ".join(normalized.casefold().strip().split())

    def _compile_contract(self, state: CompileState) -> dict[str, Any]:
        """将编译结果收敛成稳定契约，并写入首版可跨重启恢复的图状态。"""
        command = state["normalized_command"]
        intent = state.get("intent") or {}
        target = state.get("target")
        recognized = bool(self._INTENT_PATTERN.search(command))
        now = self._now()
        deadline_text = str(intent.get("deadlineText") or "")
        resolved_time_text = str(intent.get("resolvedTimeText") or "").strip()
        if not resolved_time_text:
            resolved_time_text = self._resolve_relative_deadline(deadline_text, now)
        graph_state = {
            # 从创建时就固定任务锚点。后续无论 Runtime 或桌面端重启多少次，
            # “明天”等相对日期都只以这里的创建时间为准，不按当前日期重新解释。
            "graphVersion": 2,
            # 相对时间只能在任务创建时解析一次。后续跨天运行必须始终复用这个锚点，
            # 不能把旧命令中的“明天”重新解释成新的明天。
            "taskCreatedAt": now.isoformat(),
            "taskTimezone": self._timezone_name(),
            "resolvedTimeText": resolved_time_text,
            "knownFacts": [],
            "pendingConditions": [str(intent.get("successCriteria") or "等待对方明确回应")],
            "targetEvidence": target.chat_name if target else "",
            # 主控台指令只用于创建任务，绝不作为聊天上下文或任务证据写入时间线。
            "timeline": [],
            # 任务前历史属于可选背景，不会混入本任务的完成证据时间线。
            "preTaskHistory": [],
            "historyAccessAllowed": True,
            "workingMemory": {
                "phase": "ACTIVE",
                "summary": "任务已解析，等待首次联系或对方回复",
                "knownFacts": [],
                "pendingConditions": [str(intent.get("successCriteria") or "等待对方明确回应")],
                "lastTimelineEventAt": now.isoformat(),
                "lastUpdatedAt": now.isoformat(),
            },
        }
        unresolved = recognized and target is None
        result = DelegatedTaskCompileResponse(
            recognized=recognized,
            taskType=str(intent.get("taskType") or "CONVERSATION_GOAL"),
            targetQuery=str(state.get("target_query") or intent.get("targetQuery") or ""),
            platform=target.platform if target else "",
            chatType=target.chat_type if target else "",
            chatId=target.chat_id if target else "",
            targetName=target.chat_name if target else "",
            objective=str(intent.get("objective") or command),
            successCriteria=str(intent.get("successCriteria") or "对方明确接受、拒绝或提出无法继续的条件"),
            deadlineText=deadline_text,
            confidence=0.94 if target else (0.55 if recognized else 0.0),
            clarificationQuestion="需要指定唯一联系人或群聊" if unresolved else "",
            requiresConfirmation=False,
            executionMode="AUTO_COMPLETE",
            initialProgress="任务已解析，准备联系对方" if target else "等待解析目标会话",
            stateJson=json.dumps(graph_state, ensure_ascii=False),
        )
        return {"result": result}

    def _build_timeline(self, state: RuntimeState) -> dict[str, Any]:
        """按平台发送时间排序并去重历史消息，明确区分本人、对方、代理和系统事件。"""
        runtime_input = state["runtime_input"]
        previous_state = self._safe_json(runtime_input.task.get("stateJson"))
        task_anchor = self._resolve_task_anchor(runtime_input.task, previous_state)
        rows = [*runtime_input.history, runtime_input.event]
        deduplicated: dict[str, dict[str, Any]] = {}

        # Java 的历史查询可能受分页窗口限制，因此先恢复上轮已经持久化的规范时间线。
        # 这些记录已经完成身份归一化，不能再次按缺省字段推断，否则“我/代理”会被误标为“对方”。
        for item in previous_state.get("timeline") or []:
            if not isinstance(item, dict):
                continue
            event_id = str(item.get("eventId") or "").strip()
            speaker = str(item.get("speaker") or "").strip()
            text = " ".join(str(item.get("text") or "").split())
            if not event_id or speaker not in {"我", "对方", "代理", "系统"} or not text:
                continue
            # 旧状态可能来自升级前的“最近消息”策略，必须再次按任务锚点裁剪。
            if not self._is_at_or_after_task_anchor(item.get("at"), task_anchor):
                continue
            deduplicated[event_id] = {
                "eventId": event_id,
                "at": str(item.get("at") or ""),
                "speaker": speaker,
                "text": text,
                "eventType": str(item.get("eventType") or "message").lower(),
                "direction": str(item.get("direction") or "").upper(),
                "actorType": str(item.get("actorType") or "").upper(),
                "messageOrigin": str(item.get("messageOrigin") or "").upper(),
            }

        for row in rows:
            event_type = str(row.get("eventType") or "").lower()
            raw_payload = row.get("rawPayload") if isinstance(row.get("rawPayload"), dict) else {}
            origin = str(row.get("messageOrigin") or raw_payload.get("messageOrigin") or "").upper()
            # 工作台命令是控制面事件，不属于双方聊天，也不能作为任务完成证据。
            if event_type == "delegated_task_started" or origin == "INTERNAL":
                continue
            key = str(row.get("eventId") or row.get("platformMessageId") or row.get("clientMessageId") or "")
            if not key:
                key = json.dumps(row, ensure_ascii=False, sort_keys=True)
            effective_at = str(
                row.get("sentAt") or row.get("timestamp") or row.get("receivedAt") or row.get("importedAt") or ""
            )
            # 主控台命令和任务创建前的聊天都不进入任务证据时间线。
            if not self._is_at_or_after_task_anchor(effective_at, task_anchor):
                continue
            direction = str(row.get("direction") or "").upper()
            actor = str(row.get("actorType") or "").upper()
            if actor == "OWNER" or direction == "OUTBOUND" and origin != "AGENT":
                speaker = "我"
            elif actor in {"AGENT", "SYSTEM"} or origin == "AGENT":
                speaker = "代理" if actor != "SYSTEM" else "系统"
            else:
                speaker = "对方"
            deduplicated[key] = {
                "eventId": str(row.get("eventId") or ""),
                "at": effective_at,
                "speaker": speaker,
                "text": " ".join(str(row.get("text") or "").split()),
                "eventType": event_type,
                "direction": direction,
                "actorType": actor,
                "messageOrigin": origin,
            }
        timeline = sorted(deduplicated.values(), key=lambda item: (item["at"], item["eventId"]))[-500:]
        return {"timeline": timeline, "previous_state": previous_state}

    def _build_action_timeline(self, state: ActionState) -> dict[str, Any]:
        """把发送前输入转换为运行图兼容结构，并复用同一套时间线清洗规则。"""
        action_input = state["action_input"]
        runtime_input = DelegatedTaskRuntimeInput(
            task=action_input.task,
            history=action_input.history,
            event=action_input.event,
            finalReply="",
            writeBackActions=[],
            preTaskHistory=action_input.pre_task_history,
            historyAccessAllowed=action_input.history_access_allowed,
        )
        timeline_state = self._build_timeline({"runtime_input": runtime_input})
        previous_state = timeline_state.get("previous_state") or {}
        if not isinstance(previous_state, dict):
            previous_state = {}
        # 恢复上一次已读取的观察结果，防止同一个任务在后续事件中重复拉取任务前历史。
        return {
            "runtime_input": runtime_input,
            "pre_task_history": self._normalize_pre_task_history(
                action_input.pre_task_history or previous_state.get("preTaskHistory")
            ),
            "tool_observations": list(previous_state.get("toolObservations") or []),
            **timeline_state,
        }

    async def _plan_react_action(self, state: ActionState) -> dict[str, Any]:
        """让主控台任务以普通 JSON 规划下一次受限工具调用。

        DeepSeek 的思考模式不兼容原生 ``tool_choice``，因此这里不使用 SDK 的函数调用。
        模型只返回工具意图和候选文本；真实工具仍由 Java 服务按权限执行。
        """
        action_input = state["action_input"]
        runtime_input = state["runtime_input"]
        task = action_input.task
        timeline = list(state.get("timeline") or [])
        previous_state = state.get("previous_state") or {}
        model_profile = state.get("model_profile")
        iteration = int(state.get("react_iteration") or 0) + 1
        review_feedback = " ".join(str(state.get("review_feedback") or "").split())
        tool_observations = list(state.get("tool_observations") or [])

        event = action_input.event
        # 规划器只需要普通 JSON 输出；模型不能直接越权执行动作。无论模型是否可用，
        # 最终动作都要经过下方同一组 LangChain ``@tool`` 的参数校验。
        if not hasattr(self.llm_client, "generate_reply") or not self.llm_client.is_enabled(model_profile):
            evaluation = self._fallback_tool_evaluation(
                event=event,
                previous_state=previous_state,
                reason="规划模型不可用，使用受限工具安全降级",
            )
            return {
                "evaluation": evaluation,
                "react_iteration": iteration,
                "react_trace": list(state.get("react_trace") or []),
            }

        task_anchor = self._resolve_task_anchor(task, previous_state)
        current_time = self._parse_timestamp(
            event.get("sentAt") or event.get("timestamp") or event.get("receivedAt")
        ) or self._now()
        resolved_time_text = self._resolved_task_time_text(task, previous_state, task_anchor)
        # 观察节点取得的数据优先于调用方传入的缓存，确保第二轮规划能立刻看到读取结果。
        pre_task_history = self._normalize_pre_task_history(state.get("pre_task_history"))
        if not tool_observations:
            pre_task_history = self._normalize_pre_task_history(
                action_input.pre_task_history or previous_state.get("preTaskHistory")
            )
        model_context = self.context_builder.build(
            task=task,
            timeline=timeline,
            pre_task_history=pre_task_history,
            previous_state=previous_state,
            task_created_at=task_anchor.isoformat() if task_anchor else "",
            current_time=current_time,
            resolved_time_text=resolved_time_text,
            history_access_allowed=bool(
                previous_state.get("historyAccessAllowed", action_input.history_access_allowed)
            ),
            available_tools=(tool.name for tool in self.action_tools),
        ).to_model_payload()
        system_prompt = (
            "你是 Memo Echo 主控台的任务执行规划器。只输出一个 JSON 对象，不要 Markdown。"
            "你不能直接执行任何操作，只能从以下工具中选择一个："
            "send_qq_message、get_task_pre_history、update_delegated_task、complete_delegated_task。"
            "候选消息必须是账号主人此刻实际要发送给对方的自然聊天文本，不能提到任务、Agent、工具、系统、联系人检索或内部编号。"
            "只能依据 taskGoal、任务后的 conversationTimeline、允许的 preTaskContext、workingMemory 和当前时间推理。"
            "preTaskContext 仅用于背景，不能作为任务完成证据。相对时间必须按消息自身 at 和 taskCreatedAt 理解。"
            "到了目标日期当天应说‘今晚’，不能沿用任务创建时的‘明天’。"
            "对方已经明确接受任务的成功条件时选择 complete_delegated_task，并提供来自任务后对方消息的 evidenceEventIds。"
            "若缺少背景且允许读取历史，选择 get_task_pre_history；若目前无需发言，选择 update_delegated_task。"
            "get_task_pre_history 是图内只读观察：若 toolObservations 已记录该工具，无论成功、空结果或失败，均不得再次选择它，"
            "必须依据已返回的上下文继续规划其他工具。"
            "JSON 字段固定为 tool、candidateMessage、reason、progressSummary、completionReport、knownFacts、"
            "pendingConditions、evidenceEventIds。数组字段必须是字符串数组。"
        )
        system_prompt += (
            "当 conversationTimeline 末尾连续出现多条联系人新消息时，必须把这些消息作为同一轮输入一起处理，"
            "不要只回答较早的一条。"
            "candidateMessage 不要用联系人昵称、QQ号、任务标题或内部状态作为开头，也不要机械复述任务时间槽位；"
            "除非对方正在确认该信息。"
            "若对方已经明确接受、拒绝或给出无法继续的条件，优先调用 complete_delegated_task；"
            "需要发最后一句时，把它放进 complete_delegated_task 的 candidateMessage。"
            "允许 candidateMessage 用换行拆成多条短气泡，但每一行都必须是真实要发给对方的话。"
        )
        payload = {
            "context": model_context,
            "reviewFeedback": review_feedback,
            "toolObservations": tool_observations[-3:],
        }
        planning_payload = json.dumps(payload, ensure_ascii=False)
        planned: dict[str, Any] = {}
        native_tool_call: dict[str, Any] | None = None
        if hasattr(self.llm_client, "choose_tool"):
            # 优先使用 LangChain 原生 @tool 调用。这里不强制 tool_choice，避免部分
            # OpenAI-compatible 模型在 thinking 模式下拒绝工具选择参数。
            tool_system_prompt = (
                system_prompt
                + "\n优先通过 LangChain 工具调用选择一个工具。"
                + "不要把工具名、联系人检索、任务状态写进要发送给对方的文本。"
            )
            try:
                native_tool_call = await self.llm_client.choose_tool(
                    tool_system_prompt,
                    planning_payload,
                    self.action_tools,
                    temperature=0.15,
                    model_profile=model_profile,
                )
            except Exception as exc:
                review_feedback = f"LangChain 工具调用失败，已回退 JSON 规划：{type(exc).__name__}"

        if native_tool_call:
            planned = self._planned_from_langchain_tool_call(native_tool_call)
        try:
            if not planned:
                raw = await self.llm_client.generate_reply(
                    system_prompt,
                    planning_payload,
                    temperature=0.15,
                    model_profile=model_profile,
                )
                planned = self._parse_json_object(raw)
        except Exception as exc:
            planned = {}
            review_feedback = f"规划模型调用失败：{type(exc).__name__}"

        planned_tool = str(planned.get("tool") or "").strip().lower()
        # 普通自然语言或截断 JSON 都不是一个受限工具决策。安全降级仍只选择
        # 已注册的 @tool，由事件方向决定首发、跟进或等待，避免任务静默卡住。
        if planned_tool not in self.action_tools_by_name:
            evaluation = self._fallback_tool_evaluation(
                event=event,
                previous_state=previous_state,
                reason="ReAct 规划输出无效，已安全降级",
            )
            trace = list(state.get("react_trace") or [])
            trace.append(
                {
                    "at": current_time.isoformat(),
                    "iteration": iteration,
                    "tool": str(evaluation.get("requestedTool") or "update_delegated_task"),
                    "reason": "ReAct 规划输出无效，已安全降级",
                }
            )
            return {
                "evaluation": evaluation,
                "model_context": model_context,
                "react_iteration": iteration,
                "review_feedback": review_feedback,
                "react_trace": trace[-10:],
            }

        requested_tool = planned_tool
        candidate = " ".join(str(planned.get("candidateMessage") or "").split())
        evaluation = {
            "reactManaged": True,
            "status": "COMPLETED" if requested_tool == "complete_delegated_task" else "ACTIVE",
            "requestedTool": requested_tool,
            "reason": " ".join(str(planned.get("reason") or "").split()),
            "progressSummary": " ".join(str(planned.get("progressSummary") or "任务仍在进行").split()),
            "messageInstruction": candidate,
            "completionReport": " ".join(str(planned.get("completionReport") or "").split()),
            "knownFacts": self._as_text_list(planned.get("knownFacts")),
            "pendingConditions": self._as_text_list(planned.get("pendingConditions")),
            "evidence": [],
            "evidenceEventIds": self._as_text_list(planned.get("evidenceEventIds")),
            "toolArguments": {"reactIteration": iteration},
        }
        if requested_tool == "complete_delegated_task":
            evaluation = self._repair_completion_evidence(
                evaluation, timeline, event, task, previous_state
            )
            evaluation = self._validate_completion_tool_call(
                evaluation, timeline, event, task, previous_state
            )
            evaluation["reactManaged"] = True
            # 完成证据不充分时校验器会把工具改成 update；后续必须使用校验后的
            # 工具名，不能继续拿旧的 complete 参数调用 @tool。
            requested_tool = str(evaluation.get("requestedTool") or "update_delegated_task")

        # 模型只提出意图；真正的参数边界始终由已注册的 LangChain @tool 校验。
        tool_arguments = {
            "reason": str(evaluation.get("reason") or "advance delegated task"),
            "progressSummary": str(evaluation.get("progressSummary") or "Task remains active"),
            "knownFacts": self._as_text_list(evaluation.get("knownFacts")),
            "pendingConditions": self._as_text_list(evaluation.get("pendingConditions")),
        }
        if requested_tool == "send_qq_message":
            tool_arguments["messageInstruction"] = candidate
        elif requested_tool == "complete_delegated_task":
            tool_arguments.update(
                {
                    "completionReport": str(evaluation.get("completionReport") or "Task completed"),
                    "outcome": "SUCCESS",
                    "evidence": self._as_text_list(evaluation.get("evidence")),
                    "evidenceEventIds": self._as_text_list(evaluation.get("evidenceEventIds")),
                    "finalMessageInstruction": candidate or None,
                }
            )
        elif requested_tool == "get_task_pre_history":
            tool_arguments = {
                "reason": str(evaluation.get("reason") or "Read pre-task history"),
                "queryFocus": None,
            }
        try:
            tool_intent = self.action_tools_by_name[requested_tool].invoke(tool_arguments)
            tool_arguments = dict(tool_intent["arguments"])
        except Exception as exc:
            tool_intent = self.action_tools_by_name["update_delegated_task"].invoke(
                {
                    "reason": f"declared tool validation failed: {type(exc).__name__}",
                    "progressSummary": "Rejected an invalid action and will wait for re-planning",
                    "knownFacts": [],
                    "pendingConditions": ["Wait for a valid declared tool action"],
                }
            )
            requested_tool = "update_delegated_task"
            evaluation = self._normalize_tool_decision(
                {"name": requested_tool, "arguments": tool_intent["arguments"]}
            )
            evaluation["reactManaged"] = True
            candidate = ""
            tool_arguments = dict(tool_intent["arguments"])
        evaluation["requestedTool"] = requested_tool
        evaluation["toolArguments"] = {**tool_arguments, "reactIteration": iteration}

        if candidate:
            guard_result = self.reply_guard.validate(
                candidate,
                self.context_builder.internal_terms(task),
            )
            if not guard_result.allowed:
                evaluation.update(
                    {
                        "requestedTool": "update_delegated_task",
                        "status": "ACTIVE",
                        "messageInstruction": "",
                        "reason": "候选消息包含内部控制信息，已拒绝发送",
                        "progressSummary": "候选消息需要重新规划",
                    }
                )
                review_feedback = "；".join(guard_result.reasons)

        trace = list(state.get("react_trace") or [])
        trace.append(
            {
                "at": current_time.isoformat(),
                "iteration": iteration,
                "tool": evaluation["requestedTool"],
                "reason": evaluation["reason"],
            }
        )
        return {
            "evaluation": evaluation,
            "model_context": model_context,
            "react_iteration": iteration,
            "review_feedback": review_feedback,
            "react_trace": trace[-10:],
        }

    async def _review_react_candidate(self, state: ActionState) -> dict[str, Any]:
        """审核主控台候选回复是否有依据、符合情景且不像内部系统输出。

        审核只决定放行、重写或等待；它不拥有发送权限。被打回时路由回规划节点，
        并把抽象反馈传给规划器，避免通过硬编码词表修补单个聊天案例。
        """
        evaluation = dict(state.get("evaluation") or {})
        requested_tool = str(evaluation.get("requestedTool") or "")
        candidate = " ".join(str(evaluation.get("messageInstruction") or "").split())
        if requested_tool not in {"send_qq_message", "complete_delegated_task"} or not candidate:
            return {"review_decision": "SKIP", "review_feedback": ""}

        action_input = state["action_input"]
        task = action_input.task
        guard_result = self.reply_guard.validate(
            candidate,
            self.context_builder.internal_terms(task),
        )
        if not guard_result.allowed:
            evaluation.update({"requestedTool": "update_delegated_task", "messageInstruction": ""})
            return {
                "evaluation": evaluation,
                "review_decision": "BLOCK",
                "review_feedback": "；".join(guard_result.reasons),
            }

        model_profile = state.get("model_profile")
        if not hasattr(self.llm_client, "generate_reply") or not self.llm_client.is_enabled(model_profile):
            return {"review_decision": "APPROVE", "review_feedback": ""}

        review_iteration = int(state.get("review_iteration") or 0) + 1
        system_prompt = (
            "你是聊天候选回复的情景一致性审查器。只输出 JSON，不要 Markdown。"
            "检查候选文本是否能由当前对话、任务和记忆支持，是否自然像本人聊天，是否泄露内部流程，"
            "是否重复已确认条件或编造未给出的事实。"
            "输出 verdict（APPROVE、REVISE、BLOCK）、feedback、revisedCandidateMessage。"
            "仅在候选确实缺乏依据或明显不自然时 REVISE；不要因信息不完整而替用户编造内容。"
        )
        try:
            raw = await self.llm_client.generate_reply(
                system_prompt,
                json.dumps(
                    {
                        "context": state.get("model_context") or {},
                        "candidateMessage": candidate,
                        "requestedTool": requested_tool,
                    },
                    ensure_ascii=False,
                ),
                temperature=0.05,
                model_profile=model_profile,
            )
            review = self._parse_json_object(raw)
        except Exception:
            # 审查模型短暂不可用时不能覆盖已规划的可执行文本，交由当前候选继续发送。
            return {"review_decision": "APPROVE", "review_feedback": "审查模型暂不可用，保留已规划候选"}

        verdict = str(review.get("verdict") or "APPROVE").strip().upper()
        feedback = " ".join(str(review.get("feedback") or "").split())
        revised = " ".join(str(review.get("revisedCandidateMessage") or "").split())
        if revised:
            revised_guard = self.reply_guard.validate(
                revised,
                self.context_builder.internal_terms(task),
            )
            if revised_guard.allowed:
                evaluation["messageInstruction"] = revised
            else:
                verdict = "BLOCK"
                feedback = "；".join(revised_guard.reasons)

        # 最多两次回环。第三次保留最后一个合格候选，避免低价值审查循环阻塞已授权的主控台任务。
        if verdict == "REVISE" and review_iteration < 3:
            return {
                "evaluation": evaluation,
                "review_iteration": review_iteration,
                "review_decision": "REVISE",
                "review_feedback": feedback or "请依据现有时间线重写得更自然、可证据支持",
            }
        if verdict == "BLOCK":
            evaluation.update({"requestedTool": "update_delegated_task", "messageInstruction": ""})
        return {
            "evaluation": evaluation,
            "review_iteration": review_iteration,
            "review_decision": "APPROVE" if verdict != "BLOCK" else "BLOCK",
            "review_feedback": feedback,
        }

    @staticmethod
    def _as_text_list(value: Any) -> list[str]:
        """把模型可能返回的标量或数组归一成有限长度的非空文本列表。"""
        values = value if isinstance(value, list) else [value]
        return [text for item in values if (text := " ".join(str(item or "").split()))][:30]

    @staticmethod
    def _platform_write_back_sent(actions: Any) -> bool:
        """判断平台写回是否成功，供发送后的 runtime 记账分支使用。"""
        return any(str(action).startswith("qq_write_back_sent:") for action in (actions or []))

    @staticmethod
    def _previous_state_planned_completion(previous_state: dict[str, Any]) -> bool:
        """判断上一轮动作图是否已经计划结束任务，避免写回记账覆盖完成态。"""
        working_memory = previous_state.get("workingMemory") if isinstance(previous_state.get("workingMemory"), dict) else {}
        return (
            str(previous_state.get("lastPlannedAction") or "").upper()
            in {"SEND_AND_COMPLETE", "COMPLETE_TASK"}
            or str(working_memory.get("status") or "").upper() == "COMPLETED"
            or str(working_memory.get("phase") or "").upper() == "COMPLETED"
        )

    def _fallback_tool_evaluation(
        self,
        *,
        event: dict[str, Any],
        previous_state: dict[str, Any],
        reason: str,
    ) -> dict[str, Any]:
        """模型暂不可用时选择一个保守的已声明工具，避免绕开工具层或任务静默。"""
        raw_payload = event.get("rawPayload") if isinstance(event.get("rawPayload"), dict) else {}
        event_type = str(event.get("eventType") or "").lower()
        origin = str(event.get("messageOrigin") or raw_payload.get("messageOrigin") or "").upper()
        direction = str(event.get("direction") or raw_payload.get("direction") or "").upper()
        actor = str(event.get("actorType") or raw_payload.get("actorType") or "").upper()
        event_text = " ".join(str(event.get("text") or "").split())
        # 首发幂等性由统一的写回状态维护，不能依赖已废弃的专用 kickoff 字段。
        kickoff_sent = (
            str(previous_state.get("lastWriteBackStatus") or "").upper() == "SENT"
            and str(previous_state.get("lastPlannedAction") or "").upper() == "SEND_MESSAGE"
        )

        if event_type == "delegated_task_started" and not kickoff_sent:
            tool_name = "send_qq_message"
            arguments = {
                "reason": reason,
                "progressSummary": "任务已创建，准备主动联系目标联系人",
                "messageInstruction": "根据委托目标和历史背景，生成发给目标联系人的第一条消息",
            }
        elif self._is_peer_inbound_message(
            event_type=event_type,
            event_text=event_text,
            direction=direction,
            actor=actor,
            origin=origin,
        ):
            tool_name = "send_qq_message"
            arguments = {
                "reason": reason,
                "progressSummary": "收到联系人新回复，准备继续推进任务",
                "messageInstruction": "根据对方最新回复和任务目标，生成自然、简短的后续回复",
            }
        else:
            tool_name = "update_delegated_task"
            arguments = {
                "reason": reason,
                "progressSummary": "任务仍在进行，等待新的联系人消息或规划模型恢复",
                "knownFacts": [],
                "pendingConditions": ["等待下一次可执行的任务事件"],
            }

        tool_intent = self.action_tools_by_name[tool_name].invoke(arguments)
        evaluation = self._normalize_tool_decision(
            {"name": tool_name, "arguments": tool_intent["arguments"]}
        )
        evaluation["reactManaged"] = True
        evaluation["reason"] = reason
        return evaluation

    def _route_after_react_plan(self, state: ActionState) -> str:
        """只有需要对外发送的工具意图才进入候选文本审查节点。"""
        evaluation = state.get("evaluation") or {}
        requested_tool = str(evaluation.get("requestedTool") or "")
        observations = list(state.get("tool_observations") or [])
        history_was_observed = any(
            isinstance(item, dict) and item.get("tool") == "get_task_pre_history"
            for item in observations
        )
        # 读取历史是图内观察，不允许把意图漏到 Java；同一轮最多读取一次再回到规划节点。
        if (
            bool(evaluation.get("reactManaged"))
            and requested_tool == "get_task_pre_history"
            and not history_was_observed
            and int(state.get("react_iteration") or 0) < 4
        ):
            return "observe_tool"
        if (
            bool(evaluation.get("reactManaged"))
            and requested_tool
            in {"send_qq_message", "complete_delegated_task"}
            and str(evaluation.get("messageInstruction") or "").strip()
        ):
            return "review_candidate"
        return "select_action"

    async def _observe_react_tool(self, state: ActionState) -> dict[str, Any]:
        """在图内执行任务前历史观察，并把结果回灌给下一轮 ReAct 规划。"""
        action_input = state["action_input"]
        event = action_input.event
        task = action_input.task
        previous_state = state.get("previous_state") or {}
        if not isinstance(previous_state, dict):
            previous_state = {}
        observations = list(state.get("tool_observations") or [])
        trace = list(state.get("react_trace") or [])
        history_access_allowed = bool(
            previous_state.get("historyAccessAllowed", action_input.history_access_allowed)
        )
        chat_id = str(
            event.get("chatId") or event.get("chat_id")
            or task.get("chatId") or task.get("chat_id") or ""
        ).strip()
        platform = str(event.get("platform") or task.get("platform") or "").strip()
        chat_type = str(
            event.get("chatType") or event.get("chat_type")
            or task.get("chatType") or task.get("chat_type") or ""
        ).strip()
        user_id = str(
            event.get("userId") or event.get("user_id")
            or task.get("userId") or task.get("user_id") or ""
        ).strip()
        task_anchor = self._resolve_task_anchor(task, previous_state)
        before = task_anchor.isoformat() if task_anchor else None

        # 读取历史同样必须先经过 LangChain @tool 的输入校验，禁止绕开工具直接访问服务。
        tool_arguments = dict((state.get("evaluation") or {}).get("toolArguments") or {})
        try:
            self.action_tools_by_name["get_task_pre_history"].invoke(
                {
                    "reason": str(tool_arguments.get("reason") or "Read pre-task conversation history"),
                    "queryFocus": tool_arguments.get("queryFocus"),
                }
            )
        except Exception as exc:
            return {
                "tool_observations": observations + [{
                    "tool": "get_task_pre_history",
                    "ok": False,
                    "message": f"declared tool validation failed: {type(exc).__name__}",
                    "messageCount": 0,
                }],
                "review_feedback": "History lookup arguments did not satisfy the declared tool schema",
                "review_decision": "",
            }

        # 即使调用失败也保留已有上下文，同时写入失败观察，避免模型无限重复读取。
        observed_history = self._normalize_pre_task_history(
            state.get("pre_task_history")
            or action_input.pre_task_history
            or previous_state.get("preTaskHistory")
        )
        error = ""
        if not history_access_allowed:
            error = "当前任务未授权读取任务前历史"
        elif not chat_id:
            error = "缺少目标会话，无法读取任务前历史"
        else:
            try:
                rows = await self.event_center_client.list_conversation_messages(
                    chat_id,
                    platform=platform or None,
                    chat_type=chat_type or None,
                    limit=20,
                    user_id=user_id or None,
                    before=before,
                )
                observed_history = self._normalize_pre_task_history(rows)
            except Exception as exc:
                error = f"读取任务前历史失败：{type(exc).__name__}"

        observation = {
            "tool": "get_task_pre_history",
            "ok": not bool(error),
            "message": error or "已读取任务前历史",
            "messageCount": len(observed_history),
        }
        trace.append(
            {
                "at": self._now().isoformat(),
                "iteration": int(state.get("react_iteration") or 0),
                "type": "tool_observation",
                "tool": "get_task_pre_history",
                "arguments": {"before": before or "", "limit": 20},
                "result": {
                    "ok": not bool(error),
                    "messageCount": len(observed_history),
                    "error": error,
                },
            }
        )
        return {
            "pre_task_history": observed_history,
            "tool_observations": (observations + [observation])[-3:],
            "react_trace": trace[-12:],
            "review_feedback": "",
            "review_decision": "",
        }

    @staticmethod
    def _route_after_react_review(state: ActionState) -> str:
        """审查打回时回到规划节点；放行、阻断或跳过时继续映射受限动作。"""
        return "plan_react" if state.get("review_decision") == "REVISE" else "select_action"

    async def _select_runtime_action(self, state: ActionState) -> dict[str, Any]:
        """根据当前控制事件、可信发送方向和完成结论选择唯一下一动作。"""
        action_input = state["action_input"]
        event = action_input.event
        evaluation = dict(state.get("evaluation") or {})
        timeline = list(state.get("timeline") or [])

        # 主控台委托任务允许 Agent 自主结束。ReAct 规划器有时会保守地继续发送或等待，
        # 因此动作落地前补一轮完成复核，让模型基于完整时间线决定是否提升为结束任务工具。
        evaluation = await self._maybe_promote_completion_action(
            state=state,
            evaluation=evaluation,
            timeline=timeline,
            event=event,
        )

        # ReAct 规划器已经完成了工具选择和候选消息审查。主控台任务在这里直接映射
        # 受限动作，不能再被下方遗留的事件规则覆盖，否则会出现模型选择结束却被改回等待。
        # 动作只能来自上游已校验的 @tool 意图；不再回落到下面历史遗留的事件规则。
        if True:
            requested_tool = str(evaluation.get("requestedTool") or "update_delegated_task")
            candidate = str(evaluation.get("messageInstruction") or "").strip()
            # 规划节点读取的是完整任务时间线。即使当前触发事件不是最新事件，也应该让模型
            # 基于已经合并进来的后续联系人消息做一次最终动作选择；否则连续消息会被旧事件
            # 强制降级为 WAIT，最新事件又可能因为状态已更新而不再触发回复。
            if requested_tool == "get_task_pre_history":
                # 防御性兜底：历史读取应该已在图内完成，绝不能再交给 Java 留下悬空动作。
                action = "WAIT"
            elif requested_tool == "complete_delegated_task":
                action = "SEND_AND_COMPLETE" if candidate else "COMPLETE_TASK"
            elif requested_tool == "send_qq_message" and candidate:
                action = "SEND_MESSAGE"
            else:
                action = "WAIT"
            return {
                "evaluation": evaluation,
                "selected_action": {
                    "action": action,
                    "reason": str(evaluation.get("reason") or "ReAct 已选择下一步工具"),
                    "messageInstruction": candidate if action in {"SEND_MESSAGE", "SEND_AND_COMPLETE"} else "",
                }
            }

    async def _maybe_promote_completion_action(
        self,
        *,
        state: ActionState,
        evaluation: dict[str, Any],
        timeline: list[dict[str, Any]],
        event: dict[str, Any],
    ) -> dict[str, Any]:
        """在动作落地前复核主控台任务是否已经完成。

        这里不通过硬编码字段判断结束，而是让模型读取任务目标、任务后的完整时间线、
        任务前历史、工作记忆和当前候选动作。只有模型给出可校验的联系人消息证据时，
        才把普通发送或等待动作提升为 complete_delegated_task。
        """
        action_input = state["action_input"]
        task = action_input.task
        previous_state = state.get("previous_state") or {}
        model_profile = state.get("model_profile")
        requested_tool = str(evaluation.get("requestedTool") or "").strip()
        if requested_tool in {"complete_delegated_task", "get_task_pre_history"}:
            return evaluation
        if requested_tool not in {"send_qq_message", "update_delegated_task"}:
            return evaluation
        if not hasattr(self.llm_client, "generate_reply") or not self.llm_client.is_enabled(model_profile):
            return evaluation
        if not any(self._is_peer_timeline_row(row) for row in timeline):
            return evaluation

        current_time = (
            self._parse_timestamp(event.get("sentAt") or event.get("timestamp") or event.get("receivedAt"))
            or self._now()
        )
        system_prompt = (
            "COMPLETION_REFLECTION\n"
            "你是 Memo Echo 主控台委托任务的完成复核器。只输出 JSON 对象，不要 Markdown。\n"
            "你的职责是在动作落地前判断任务是否已经被联系人明确完成、拒绝或无法继续。\n"
            "只能依据任务目标、成功条件、任务创建后的 conversationTimeline、允许的 preTaskContext、"
            "workingMemory、当前候选动作和时间戳。\n"
            "preTaskContext 只能用于背景理解，不能作为完成证据。\n"
            "只有任务创建后的联系人消息能作为 evidenceEventIds；账号主人、Agent、系统、内部命令和任务创建前历史都不能作为完成证据。\n"
            "不要只看最后一条消息，多条联系人消息可以共同证明任务已完成。\n"
            "如果证据不足，返回 shouldComplete=false。\n"
            "如果已完成，输出 shouldComplete=true，并填写 reason、progressSummary、completionReport、"
            "finalMessageInstruction、knownFacts、pendingConditions、evidence、evidenceEventIds。\n"
            "finalMessageInstruction 只在结束前还需要自然收尾时填写；不要称呼联系人昵称、QQ号或任务标题。"
        )
        payload = {
            "task": task,
            "previousState": previous_state,
            "currentEvaluation": evaluation,
            "currentEvent": event,
            "timeline": timeline[-500:],
            "context": state.get("model_context") or {},
            "toolObservations": list(state.get("tool_observations") or []),
            "currentTime": current_time.isoformat(),
        }
        try:
            raw = await self.llm_client.generate_reply(
                system_prompt,
                json.dumps(payload, ensure_ascii=False),
                temperature=0.1,
                model_profile=model_profile,
            )
            parsed = self._parse_json_object(raw)
        except Exception:
            return self._fallback_completion_evaluation(
                state=state,
                evaluation=evaluation,
                timeline=timeline,
                event=event,
                task=task,
                previous_state=previous_state,
            ) or evaluation
        if not parsed or not parsed.get("shouldComplete"):
            return self._fallback_completion_evaluation(
                state=state,
                evaluation=evaluation,
                timeline=timeline,
                event=event,
                task=task,
                previous_state=previous_state,
            ) or evaluation

        tool = self.action_tools_by_name.get("complete_delegated_task")
        if tool is None:
            return evaluation

        outcome = str(parsed.get("outcome") or "SUCCESS").upper()
        if outcome not in {"SUCCESS", "REJECTED", "BLOCKED"}:
            outcome = "SUCCESS"
        reason = str(parsed.get("reason") or "任务完成复核确认已满足结束条件")
        progress_summary = str(parsed.get("progressSummary") or "任务已完成")
        completion_report = str(parsed.get("completionReport") or reason)
        final_message = str(parsed.get("finalMessageInstruction") or "").strip() or None

        try:
            tool_result = tool.invoke(
                {
                    "reason": reason,
                    "progressSummary": progress_summary,
                    "completionReport": completion_report,
                    "outcome": outcome,
                    "evidence": self._as_text_list(parsed.get("evidence")),
                    "evidenceEventIds": self._as_text_list(parsed.get("evidenceEventIds")),
                    "knownFacts": self._as_text_list(parsed.get("knownFacts")),
                    "pendingConditions": self._as_text_list(parsed.get("pendingConditions")),
                    "finalMessageInstruction": final_message,
                }
            )
            arguments = tool_result.get("arguments") if isinstance(tool_result, dict) else {}
            promoted = self._normalize_tool_decision(
                {"name": "complete_delegated_task", "arguments": arguments}
            )
            promoted["reactManaged"] = True
            promoted["completionReflection"] = True
            promoted = self._repair_completion_evidence(promoted, timeline, event, task, previous_state)
            promoted = self._validate_completion_tool_call(promoted, timeline, event, task, previous_state)
            if promoted.get("requestedTool") == "complete_delegated_task":
                return promoted
        except Exception:
            return evaluation
        return evaluation

    def _fallback_completion_evaluation(
        self,
        *,
        state: ActionState,
        evaluation: dict[str, Any],
        timeline: list[dict[str, Any]],
        event: dict[str, Any],
        task: dict[str, Any],
        previous_state: dict[str, Any],
    ) -> dict[str, Any] | None:
        """在完成复核模型失效时，使用保守证据兜底结束主控台任务。

        这里不是替代 ReviewAgent 的语义判断，而是防止模型返回非 JSON、工具模式报错或超时后，
        明确已经收尾的主控台任务一直卡在“代理中”。兜底必须同时满足三个条件：
        1. 任务创建后已经由 Agent/用户向联系人发起过推进消息；
        2. 当前没有更新的联系人消息压过本轮事件；
        3. 任务创建后的联系人消息给出了明确接受、拒绝、取消或自然收尾。
        """
        tool = self.action_tools_by_name.get("complete_delegated_task")
        if tool is None:
            return None
        if self._has_newer_peer_message_after_current_event(timeline, event):
            return None

        signal = self._latest_peer_completion_signal(timeline, event, task, previous_state)
        if signal is None:
            return None

        outcome = signal["outcome"]
        text = signal["text"]
        event_id = signal["eventId"]
        reason = f"联系人已给出可核验的任务结果：{text}"
        progress_summary = "任务已具备结束条件"
        completion_report = f"已根据联系人回复结束任务：{text}"

        try:
            tool_result = tool.invoke(
                {
                    "reason": reason,
                    "progressSummary": progress_summary,
                    "completionReport": completion_report,
                    "outcome": outcome,
                    "evidence": [text],
                    "evidenceEventIds": [event_id],
                    "knownFacts": [text],
                    "pendingConditions": [],
                    "finalMessageInstruction": None,
                }
            )
            arguments = tool_result.get("arguments") if isinstance(tool_result, dict) else {}
            promoted = self._normalize_tool_decision(
                {"name": "complete_delegated_task", "arguments": arguments}
            )
            promoted["reactManaged"] = True
            promoted["completionReflection"] = "fallback"
            promoted = self._repair_completion_evidence(promoted, timeline, event, task, previous_state)
            promoted = self._validate_completion_tool_call(promoted, timeline, event, task, previous_state)
            if promoted.get("requestedTool") == "complete_delegated_task":
                return promoted
        except Exception:
            return None
        return None

    def _latest_peer_completion_signal(
        self,
        timeline: list[dict[str, Any]],
        event: dict[str, Any],
        task: dict[str, Any],
        previous_state: dict[str, Any],
    ) -> dict[str, str] | None:
        """从任务后的时间线里提取最近一条可作为结束证据的联系人消息。"""
        task_anchor = self._resolve_task_anchor(task, previous_state)
        if not self._has_prior_outbound_after_anchor(timeline, task_anchor, previous_state):
            return None

        for row in reversed(timeline):
            if not self._is_peer_timeline_row(row):
                continue
            if not self._is_at_or_after_task_anchor(row.get("at"), task_anchor):
                continue
            text = str(row.get("text") or "").strip()
            outcome = self._completion_outcome_from_text(text)
            if not outcome:
                continue
            event_id = str(row.get("eventId") or event.get("eventId") or "").strip()
            if not event_id:
                continue
            return {"eventId": event_id, "text": text, "outcome": outcome}
        return None

    def _has_prior_outbound_after_anchor(
        self,
        timeline: list[dict[str, Any]],
        task_anchor: datetime | None,
        previous_state: dict[str, Any],
    ) -> bool:
        """判断任务创建后是否已经主动向联系人推进过，避免把第一条联系人消息误判为结束。"""
        last_status = str(previous_state.get("lastWriteBackStatus") or "").upper()
        last_action = str(previous_state.get("lastPlannedAction") or "").upper()
        if last_status == "SENT" and last_action in {"SEND_MESSAGE", "SEND_AND_COMPLETE"}:
            return True

        for row in timeline:
            if not isinstance(row, dict):
                continue
            text = str(row.get("text") or "").strip()
            if not text:
                continue
            if not self._is_at_or_after_task_anchor(row.get("at"), task_anchor):
                continue
            direction = str(row.get("direction") or "").upper()
            actor = str(row.get("actorType") or "").upper()
            origin = str(row.get("messageOrigin") or "").upper()
            speaker = str(row.get("speaker") or "").strip().lower()
            if direction == "OUTBOUND" or actor in {"OWNER", "AGENT"}:
                return True
            if origin in {"AGENT", "AGENT_AUTO", "AGENT_CONFIRMED", "USER_MANUAL"}:
                return True
            if speaker in {"我", "owner", "agent", "代理"}:
                return True
        return False

    def _completion_outcome_from_text(self, text: str) -> str | None:
        """把联系人自然收尾语义归一为任务结果；无法确定时返回 None。"""
        normalized = self._normalize_chat_text(text)
        if not normalized or self._is_question_like_completion_text(normalized):
            return None
        if len(normalized) > 80:
            return None

        rejected_tokens = (
            "不行",
            "不可以",
            "没空",
            "不方便",
            "算了",
            "取消",
            "不用了",
            "办不了",
            "改不了",
            "没办法",
        )
        if any(token in normalized for token in rejected_tokens):
            return "REJECTED"

        accepted_tokens = (
            "好",
            "好的",
            "可以",
            "行",
            "ok",
            "OK",
            "没问题",
            "收到",
            "知道了",
            "了解",
            "明白",
            "就这么定",
            "定了",
            "见",
            "到时候见",
            "明晚见",
            "今晚见",
            "晚上见",
        )
        return "SUCCESS" if any(token in normalized for token in accepted_tokens) else None

    @staticmethod
    def _normalize_chat_text(text: str) -> str:
        """清理聊天文本中的空白和结尾标点，便于做保守兜底判断。"""
        normalized = unicodedata.normalize("NFKC", str(text or "")).strip()
        normalized = re.sub(r"\s+", "", normalized)
        return normalized.strip("。.!！~～")

    @staticmethod
    def _is_question_like_completion_text(normalized_text: str) -> bool:
        """识别仍在协商中的反问句，避免把“可以吗”提前当成完成。"""
        if not normalized_text:
            return False
        question_markers = ("?", "？", "吗", "嘛", "么", "呢", "咋", "怎么", "多少", "哪个", "哪", "啥")
        if normalized_text.endswith(question_markers):
            return True
        negotiation_markers = (
            "可以吗",
            "行吗",
            "方便吗",
            "可不可以",
            "能不能",
            "要不要",
            "还是",
            "或者",
            "改到",
        )
        return any(marker in normalized_text for marker in negotiation_markers)

    @staticmethod
    def _is_peer_inbound_message(
        *,
        event_type: str,
        event_text: str,
        direction: str,
        actor: str,
        origin: str,
    ) -> bool:
        """判断当前事件是否为联系人新回复，并兼容 NapCat 未提供 direction 的事件。

        NapCat 使用 ``message`` 表示收到的消息，使用 ``message_sent`` 表示当前账号
        发出的回显。因此 direction 缺失时，可以由事件类型、参与者和来源联合推断；
        但只要出现明确的出站、自身或内部证据，就必须拒绝，避免 Agent 自回复循环。
        """
        # 新事件应提供 message 类型；旧持久化记录可能只有明确的 INBOUND 方向。
        # 类型缺失时允许继续依据方向判断，但明确的非消息事件仍不能触发回复或完成任务。
        if not event_text or (event_type and event_type != "message"):
            return False
        if direction == "OUTBOUND" or actor in {"OWNER", "AGENT", "SYSTEM"}:
            return False
        if origin in {
            "INTERNAL",
            "AGENT",
            "AGENT_AUTO",
            "AGENT_CONFIRMED",
            "USER_MANUAL",
        }:
            return False
        if direction == "INBOUND" or actor == "CONTACT":
            return True
        # 连接器旧事件没有 direction；EXTERNAL + message 是平台联系人入站的可靠组合。
        return origin in {"EXTERNAL", "PLATFORM"}

    def _is_peer_timeline_row(self, row: dict[str, Any]) -> bool:
        """判断一条时间线记录是否来自联系人。

        旧版本只看 ``speaker == "对方"``，但重启、历史导入或 Java 补偿写入时，
        speaker 可能为空或被本地化处理。这里优先使用方向、参与者类型和消息来源，
        只把明确来自外部联系人的文本消息当作完成任务的证据。
        """
        if not isinstance(row, dict):
            return False
        text = str(row.get("text") or "").strip()
        if not text:
            return False
        event_type = str(row.get("eventType") or "message").lower()
        direction = str(row.get("direction") or "").upper()
        actor = str(row.get("actorType") or "").upper()
        origin = str(row.get("messageOrigin") or "").upper()
        speaker = str(row.get("speaker") or "").strip().lower()
        if speaker in {"我", "owner", "agent", "代理", "system", "系统"}:
            return False
        if speaker in {"对方", "contact", "peer", "external"}:
            return True
        return self._is_peer_inbound_message(
            event_type=event_type,
            event_text=text,
            direction=direction,
            actor=actor,
            origin=origin,
        )

    def _has_newer_peer_message_after_current_event(
        self,
        timeline: list[dict[str, Any]],
        event: dict[str, Any],
    ) -> bool:
        """判断当前事件之后是否已经出现新的联系人消息。

        连续收到多条消息时，较早事件的处理可能比后续事件更晚完成。
        如果这时继续发送旧事件的候选回复，就会出现“回上一句、不看最新一句”的问题。
        这里仅做顺序保护：旧事件降级为等待，最新事件负责合并上下文后回复。
        """
        current_event_id = str(event.get("eventId") or "").strip()
        if current_event_id:
            current_index: int | None = None
            for index, row in enumerate(timeline):
                if str(row.get("eventId") or "").strip() == current_event_id:
                    current_index = index
                    break
            if current_index is not None:
                return any(self._is_peer_timeline_row(row) for row in timeline[current_index + 1 :])

        current_at = self._parse_timestamp(
            event.get("sentAt") or event.get("timestamp") or event.get("receivedAt")
        )
        if not current_at:
            return False
        for row in timeline:
            if not self._is_peer_timeline_row(row):
                continue
            row_at = self._parse_timestamp(row.get("at"))
            if row_at and row_at > current_at:
                return True
        return False

    def _repair_completion_evidence(
        self,
        evaluation: dict[str, Any],
        timeline: list[dict[str, Any]],
        event: dict[str, Any],
        task: dict[str, Any],
        previous_state: dict[str, Any],
    ) -> dict[str, Any]:
        """补全完成工具的联系人证据。

        模型经常能判断“任务完成”，但漏填 evidenceEventIds。主控台任务是否结束
        不能只靠模型一句判断，所以这里从当前事件和任务后时间线中补齐联系人消息 ID，
        再交给统一校验函数检查任务锚点和消息来源。
        """
        if str(evaluation.get("requestedTool") or "").lower() != "complete_delegated_task":
            return evaluation

        task_anchor = self._resolve_task_anchor(task, previous_state)
        timeline_by_id = {
            str(row.get("eventId") or ""): row
            for row in timeline
            if isinstance(row, dict) and str(row.get("eventId") or "").strip()
        }
        valid_ids: list[str] = []
        requested_ids = self._as_text_list(evaluation.get("evidenceEventIds"))
        for evidence_id in requested_ids:
            row = timeline_by_id.get(evidence_id)
            if not row or not self._is_peer_timeline_row(row):
                continue
            if not self._is_at_or_after_task_anchor(row.get("at"), task_anchor):
                continue
            valid_ids.append(evidence_id)

        # 模型主动给了证据但证据无效时，不能私自换成当前消息；
        # 这类情况通常说明模型把任务前历史或普通追问误当作完成证据。
        if requested_ids and not valid_ids:
            return evaluation

        if not valid_ids:
            current_id = str(event.get("eventId") or "").strip()
            current_row = timeline_by_id.get(current_id)
            if (
                current_id
                and current_row
                and self._is_peer_timeline_row(current_row)
                and self._is_at_or_after_task_anchor(current_row.get("at"), task_anchor)
            ):
                valid_ids.append(current_id)

        if not valid_ids:
            for row in reversed(timeline):
                if not isinstance(row, dict):
                    continue
                row_id = str(row.get("eventId") or "").strip()
                if not row_id or row_id in valid_ids:
                    continue
                if not self._is_peer_timeline_row(row):
                    continue
                if not self._is_at_or_after_task_anchor(row.get("at"), task_anchor):
                    continue
                valid_ids.append(row_id)
                if len(valid_ids) >= 5:
                    break
            valid_ids.reverse()

        if not valid_ids:
            return evaluation

        evidence = [
            str(timeline_by_id[evidence_id].get("text") or "").strip()
            for evidence_id in valid_ids
            if evidence_id in timeline_by_id and str(timeline_by_id[evidence_id].get("text") or "").strip()
        ]
        completion_report = str(evaluation.get("completionReport") or "").strip()
        if not completion_report:
            completion_report = "联系人已给出满足任务完成条件的明确回复，任务可以结束。"
        return {
            **evaluation,
            "evidenceEventIds": valid_ids,
            "evidence": evidence or self._as_text_list(evaluation.get("evidence")),
            "completionReport": completion_report,
        }

    def _finalize_action(self, state: ActionState) -> dict[str, Any]:
        """把动作选择合并进可恢复状态，并映射到受权限控制的工具名称。"""
        action_input = state["action_input"]
        evaluation = state.get("evaluation") or {}
        selected = state.get("selected_action") or {}
        previous = state.get("previous_state") or {}
        action = str(selected.get("action") or "WAIT").upper()
        final_requested_tool = str(evaluation.get("requestedTool") or "").strip()
        # evaluation 是 ReAct 工具选择和审查节点修正后的最终意图。
        # 如果仍然以 selected_action 为准，“发送后发现任务已完成”的提升会被旧动作覆盖。
        if final_requested_tool == "complete_delegated_task":
            action = (
                "SEND_AND_COMPLETE"
                if str(evaluation.get("messageInstruction") or "").strip()
                else "COMPLETE_TASK"
            )
            requested_tool = "complete_delegated_task"
        elif final_requested_tool == "send_qq_message":
            action = "SEND_MESSAGE"
            requested_tool = "send_qq_message"
        elif final_requested_tool == "get_task_pre_history":
            action = "READ_PRE_HISTORY"
            requested_tool = "get_task_pre_history"
        elif final_requested_tool == "update_delegated_task":
            action = "WAIT"
            requested_tool = "update_delegated_task"
        else:
            requested_tool = {
                "SEND_MESSAGE": "send_qq_message",
                "SEND_AND_COMPLETE": "complete_delegated_task",
                "COMPLETE_TASK": "complete_delegated_task",
                "READ_PRE_HISTORY": "get_task_pre_history",
            }.get(action, "update_delegated_task")
        final_reason = str(evaluation.get("reason") or selected.get("reason") or "").strip()
        final_message_instruction = str(
            evaluation.get("messageInstruction") or selected.get("messageInstruction") or ""
        ).strip()
        progress = str(evaluation.get("progressSummary") or final_reason or "任务仍在进行")
        completion_report = (
            str(evaluation.get("completionReport") or "")
            if requested_tool == "complete_delegated_task"
            else ""
        )
        task_anchor = self._resolve_task_anchor(action_input.task, previous)
        resolved_time_text = self._resolved_task_time_text(
            action_input.task,
            previous,
            task_anchor,
        )
        known_facts = self._merge_unique(previous.get("knownFacts"), evaluation.get("knownFacts"))
        pending_conditions = (
            []
            if requested_tool == "complete_delegated_task"
            else self._merge_unique(previous.get("pendingConditions"), evaluation.get("pendingConditions"))
        )
        timeline = list(state.get("timeline") or [])[-500:]
        tool_observations = list(state.get("tool_observations") or [])
        pre_task_history = self._normalize_pre_task_history(state.get("pre_task_history"))
        if not tool_observations:
            pre_task_history = self._normalize_pre_task_history(
                action_input.pre_task_history or previous.get("preTaskHistory")
            )
        history_access_allowed = bool(
            previous.get("historyAccessAllowed", action_input.history_access_allowed)
        )
        graph_state = {
            **previous,
            "graphVersion": 3,
            "taskCreatedAt": (
                str(previous.get("taskCreatedAt") or "")
                or self._task_created_at_text(action_input.task)
            ),
            "taskTimezone": str(previous.get("taskTimezone") or self._timezone_name()),
            "resolvedTimeText": resolved_time_text,
            "knownFacts": known_facts,
            "pendingConditions": pending_conditions,
            "workingMemory": self._build_working_memory(
                previous=previous,
                progress=progress,
                known_facts=known_facts,
                pending_conditions=pending_conditions,
                status="COMPLETED" if requested_tool == "complete_delegated_task" else "ACTIVE",
                timeline=timeline,
            ),
            "preTaskHistory": pre_task_history,
            # 只保存小型观察摘要，避免任务状态无限膨胀，同时抑制后续事件重复读历史。
            "toolObservations": tool_observations[-3:],
            "historyAccessAllowed": history_access_allowed,
            "timeline": timeline,
            "lastPlannedAction": action,
            "lastPlannedAt": datetime.now(timezone.utc).isoformat(),
            "lastEvidence": list(evaluation.get("evidence") or [])[:10],
            "lastEvidenceEventIds": list(evaluation.get("evidenceEventIds") or [])[:10],
            # 这个字段用于平台写回后的 runtime 记账阶段确认完成态。
            # 如果动作图已经计划“发送并完成”，runtime 只能依据真实写回结果确认或回滚，不能重新把任务降级为进行中。
            "lastCompletionReport": completion_report if action in {"COMPLETE_TASK", "SEND_AND_COMPLETE"} else "",
            # 仅保存最近有限轨迹，供下一轮模型理解已调用的工具和审查结论。
            "reactTrace": list(state.get("react_trace") or [])[-10:],
            "lastReviewDecision": str(state.get("review_decision") or ""),
            "lastReviewFeedback": str(state.get("review_feedback") or "")[:400],
        }
        result = DelegatedTaskActionDecision(
            action=action,
            reason=final_reason,
            progressSummary=progress,
            messageInstruction=final_message_instruction if action in {"SEND_MESSAGE", "SEND_AND_COMPLETE"} else "",
            stateJson=json.dumps(graph_state, ensure_ascii=False),
            lastEventId=str(action_input.event.get("eventId") or ""),
            completionReport=completion_report,
            evidence=list(evaluation.get("evidence") or [])[:10],
            requestedTool=requested_tool,
            toolArguments={
                **dict(evaluation.get("toolArguments") or {}),
                "action": action,
                "evidence": list(evaluation.get("evidence") or [])[:10],
                "evidenceEventIds": list(evaluation.get("evidenceEventIds") or [])[:10],
                # 发送 Agent 据此使用审查后的候选文本，不重新生成第二个版本。
                "reactManaged": bool(evaluation.get("reactManaged")),
                "finalCandidateMessage": (
                    final_message_instruction
                    if bool(evaluation.get("reactManaged"))
                    else ""
                ),
            },
        )
        return {"result": result}

    async def _evaluate_runtime(self, state: RuntimeState) -> dict[str, Any]:
        """让主控台委托 Agent 基于完整任务记忆选择下一工具。

        动作图会调用模型选择工具；发送后的运行图只记录真实写回结果，不再进行
        第二次语义判断。这样同一轮不会先决定回复、发送后又被另一次判断误结束。
        """
        runtime_input = state["runtime_input"]
        task = runtime_input.task
        timeline = state["timeline"]
        previous_state = state.get("previous_state") or {}

        # runtime_graph 仅在消息已经写回平台后调用，此时绝不再次让模型选择工具。
        if runtime_input.final_reply or runtime_input.write_back_actions:
            sent = self._platform_write_back_sent(runtime_input.write_back_actions)
            if self._previous_state_planned_completion(previous_state):
                working_memory = previous_state.get("workingMemory") if isinstance(previous_state.get("workingMemory"), dict) else {}
                completion_report = str(
                    previous_state.get("lastCompletionReport")
                    or working_memory.get("progress")
                    or working_memory.get("summary")
                    or "任务已完成"
                ).strip()
                if sent:
                    return {
                        "evaluation": {
                            "status": "COMPLETED",
                            "requestedTool": "complete_delegated_task",
                            "reason": "上游动作图已计划完成，且平台写回成功，确认完成态",
                            "progressSummary": completion_report,
                            "completionReport": completion_report,
                            "knownFacts": self._as_text_list(previous_state.get("knownFacts")),
                            "pendingConditions": [],
                            "evidence": self._as_text_list(previous_state.get("lastEvidence")),
                            "evidenceEventIds": self._as_text_list(previous_state.get("lastEvidenceEventIds")),
                            "toolArguments": {
                                "reactManaged": True,
                                "writeBackSucceeded": True,
                            },
                        }
                    }
                return {
                    "evaluation": {
                        "status": "ACTIVE",
                        "requestedTool": "update_delegated_task",
                        "reason": "上游动作图已计划完成，但平台写回未成功，暂不结束任务",
                        "progressSummary": "收尾消息发送失败，任务保持进行中",
                        "completionReport": "",
                        "knownFacts": self._as_text_list(previous_state.get("knownFacts")),
                        "pendingConditions": ["等待平台写回成功后再结束任务"],
                        "evidence": self._as_text_list(previous_state.get("lastEvidence")),
                        "evidenceEventIds": self._as_text_list(previous_state.get("lastEvidenceEventIds")),
                        "toolArguments": {
                            "reactManaged": True,
                            "writeBackSucceeded": False,
                        },
                    }
                }
            return {
                "evaluation": {
                    "status": "ACTIVE",
                    "requestedTool": "update_delegated_task",
                    "reason": "记录本轮平台写回结果",
                    "progressSummary": "已发送消息，等待联系人回复" if sent else "任务保持进行中",
                    "completionReport": "",
                    "knownFacts": [],
                    "pendingConditions": [str(task.get("successCriteria") or "等待任务目标达成")],
                    "evidence": [],
                    "evidenceEventIds": [],
                    "toolArguments": {},
                }
            }

        event = runtime_input.event
        raw_payload = event.get("rawPayload") if isinstance(event.get("rawPayload"), dict) else {}
        peer_inbound = self._is_peer_inbound_message(
            event_type=str(event.get("eventType") or "").lower(),
            event_text=str(event.get("text") or "").strip(),
            direction=str(event.get("direction") or raw_payload.get("direction") or "").upper(),
            actor=str(event.get("actorType") or raw_payload.get("actorType") or "").upper(),
            origin=str(event.get("messageOrigin") or raw_payload.get("messageOrigin") or "").upper(),
        )
        default_evaluation = {
            "status": "ACTIVE",
            "requestedTool": "send_qq_message" if peer_inbound else "update_delegated_task",
            "reason": "模型不可用时不允许系统自行结束任务",
            "progressSummary": "联系人有新回复，继续推进任务" if peer_inbound else "等待联系人回复",
            "completionReport": "",
            "knownFacts": [],
            "pendingConditions": [str(task.get("successCriteria") or "等待任务目标达成")],
            "evidence": [],
            "evidenceEventIds": [],
            "messageInstruction": "依据目标、历史和联系人最新回复继续自然对话" if peer_inbound else "",
            "toolArguments": {},
        }
        model_profile = state.get("model_profile")
        if not self.llm_client.is_enabled(model_profile) or not timeline:
            return {"evaluation": default_evaluation}

        system_prompt = (
            "你是 Memo Echo 主控台委托任务的执行 Agent。"
            "请读取任务目标、成功条件、持久化状态和带时间戳的双方历史，然后只选择一个工具。"
            "send_qq_message 用于继续对话；update_delegated_task 用于无需回复但仍要等待；"
            "complete_delegated_task 用于任务创建后的联系人消息已经共同证明目标成功、明确拒绝或确定无法继续。"
            "完成结论必须综合任务创建后的多轮对话，不要求最后一条消息独自包含全部条件。"
            "如果任务已经可以结束，但联系人最后一句沿用了跨天后已经过时的‘明天/明晚’等时间口径，"
            "不要因此抹掉此前有效确认；仍选择 complete_delegated_task，并在 finalMessageInstruction 中要求"
            "先发送一句符合 currentTime 和 resolvedTimeText 的简短纠正或收尾消息。"
            "例如目标日期就是 currentTime 当天，而联系人说‘明天见’，应要求回复‘今晚见’，发送成功后结束任务。"
            "不得把用户工作台命令、本人消息、代理消息或任务创建前的旧聊天当作完成证据。"
            "结束任务时 evidenceEventIds 必须引用时间线中任务创建后由对方发送的真实事件；"
            "当前事件必须是触发本轮判断的联系人入站消息，但证据可以由当前消息和此前多条任务内消息共同组成。"
            "命令中的相对时间必须以 taskCreatedAt 为基准解释，历史消息里的相对时间必须以各自 at 为基准解释。"
            "绝不能在跨天后把旧消息中的‘明天’重新按 currentTime 解释。"
            "生成当前回复时要按 currentTime 选择今天、今晚或明天等表述；"
            "例如任务创建日说的‘明天晚上’，到了目标日期当天应说‘今晚’，不能说‘明天晚上’。"
            "不确定是否结束时必须继续对话，不要为了收尾而猜测。"
            "当对方已经明确接受安排、确认具体时间，或使用‘好的/可以/就这么定/到时见’等语义完成了当前任务时，"
            "应优先选择 complete_delegated_task，而不是继续围绕同一时间、地点或条件重复提问。"
            "若仍有一个必要信息未确认，只能询问那个缺失信息；不要把任务目标中的全部字段再次复述给对方。"
            "工具参数应保存当前进度、已知事实和待满足条件；messageInstruction 只描述下一条回复应完成什么。"
        )
        system_prompt += (
            "任务创建后的 timeline 才是当前任务的执行证据；preTaskHistory 仅用于理解背景，绝不能作为完成证据。"
            "只有 timeline、persistentState 与任务字段均不足以理解当前背景，且 historyAccessAllowed 为 true、preTaskHistory 为空时，"
            "才可调用 get_task_pre_history。没有权限或仍无依据时应 update_delegated_task，不得编造事实。"
        )
        task_anchor = self._resolve_task_anchor(task, previous_state)
        resolved_time_text = self._resolved_task_time_text(task, previous_state, task_anchor)
        payload = {
            "taskId": task.get("id"),
            "objective": task.get("objective"),
            "successCriteria": task.get("successCriteria"),
            "deadlineText": task.get("deadlineText"),
            "taskCreatedAt": task_anchor.isoformat() if task_anchor else "",
            "taskTimezone": str(previous_state.get("taskTimezone") or self._timezone_name()),
            "resolvedTimeText": resolved_time_text,
            # 回放或延迟消费事件时，应以消息真实发生时间理解“今天/今晚”，
            # 不能把服务进程当前时间误当成会话发生时间。
            "currentTime": (
                self._parse_timestamp(
                    event.get("sentAt")
                    or event.get("timestamp")
                    or event.get("receivedAt")
                )
                or self._now()
            ).isoformat(),
            "persistentState": previous_state,
            "workingMemory": previous_state.get("workingMemory") or {},
            "historyAccessAllowed": runtime_input.history_access_allowed,
            "preTaskHistory": self._normalize_pre_task_history(runtime_input.pre_task_history)[-30:],
            "timeline": timeline[-500:],
            "currentEventId": str(event.get("eventId") or ""),
        }
        # 旧 payload 仍在这里供编排层后续兼容使用；模型实际只接收安全工作上下文。
        payload = self.context_builder.build(
            task=task,
            timeline=timeline,
            pre_task_history=self._normalize_pre_task_history(runtime_input.pre_task_history),
            previous_state=previous_state,
            task_created_at=task_anchor.isoformat() if task_anchor else "",
            current_time=self._parse_timestamp(
                event.get("sentAt") or event.get("timestamp") or event.get("receivedAt")
            ) or self._now(),
            resolved_time_text=resolved_time_text,
            history_access_allowed=runtime_input.history_access_allowed,
            available_tools=(tool.name for tool in self.action_tools),
        ).to_model_payload()
        # 此分支仅作为旧入口的安全兜底。动作规划必须由上游 ReAct 节点通过
        # LangChain @tool 完成参数校验，不能再次让模型走另一套函数调用协议。
        try:
            tool_intent = self.action_tools_by_name["update_delegated_task"].invoke(
                {
                    "reason": "等待上游 ReAct 节点生成经 @tool 校验的动作意图",
                    "progressSummary": "任务保持当前进度，等待下一条可处理消息",
                    "knownFacts": [],
                    "pendingConditions": [],
                    "evidence": [],
                    "evidenceEventIds": [],
                }
            )
            evaluation = self._normalize_tool_decision(
                {"name": "update_delegated_task", "arguments": tool_intent["arguments"]}
            )
        except Exception:
            return {"evaluation": default_evaluation}

        if evaluation.get("requestedTool") == "complete_delegated_task":
            evaluation = self._validate_completion_tool_call(
                evaluation,
                timeline,
                event,
                task,
                previous_state,
            )
        return {"evaluation": evaluation}

    @staticmethod
    def _planned_from_langchain_tool_call(tool_call: dict[str, Any]) -> dict[str, Any]:
        """把 LangChain tool_call 转成旧规划字典，复用后续 @tool 参数校验和审查节点。"""
        name = str(tool_call.get("name") or "").strip()
        arguments = tool_call.get("arguments") or {}
        if not isinstance(arguments, dict):
            arguments = {}
        planned: dict[str, Any] = {"tool": name, **arguments}
        if name == "send_qq_message" and not planned.get("candidateMessage"):
            planned["candidateMessage"] = (
                arguments.get("messageInstruction")
                or arguments.get("message_instruction")
                or ""
            )
        if name == "complete_delegated_task" and not planned.get("candidateMessage"):
            planned["candidateMessage"] = (
                arguments.get("finalMessageInstruction")
                or arguments.get("final_message_instruction")
                or arguments.get("messageInstruction")
                or arguments.get("message_instruction")
                or ""
            )
        field_aliases = {
            "progressSummary": "progress_summary",
            "completionReport": "completion_report",
            "knownFacts": "known_facts",
            "pendingConditions": "pending_conditions",
            "evidenceEventIds": "evidence_event_ids",
        }
        for camel, snake in field_aliases.items():
            if not planned.get(camel):
                planned[camel] = arguments.get(snake) or ([] if camel.endswith("Ids") or camel in {"knownFacts", "pendingConditions"} else "")
        if not planned.get("reason"):
            planned["reason"] = arguments.get("reason") or "LangChain tool call"
        return planned

    @staticmethod
    def _normalize_tool_decision(tool_call: dict[str, Any]) -> dict[str, Any]:
        """把模型原生工具调用转换成图内统一状态，不根据聊天关键词推断业务结果。"""
        name = str(tool_call.get("name") or "").strip()
        allowed = {
            "send_qq_message",
            "update_delegated_task",
            "complete_delegated_task",
            "get_task_pre_history",
        }
        if name not in allowed:
            raise ValueError(f"unsupported delegated task tool: {name}")
        arguments = tool_call.get("arguments") if isinstance(tool_call.get("arguments"), dict) else {}

        def read_argument(*keys: str, default: Any = None) -> Any:
            """同时兼容 camelCase 与 LangChain Schema 默认的 snake_case 参数名。"""
            for key in keys:
                value = arguments.get(key)
                if value is not None:
                    return value
            return default

        completed = name == "complete_delegated_task"
        return {
            "status": "COMPLETED" if completed else "ACTIVE",
            "requestedTool": name,
            "reason": str(read_argument("reason") or ""),
            "progressSummary": str(read_argument("progressSummary", "progress_summary") or "任务仍在进行"),
            "messageInstruction": str(
                read_argument("finalMessageInstruction", "final_message_instruction")
                or read_argument("messageInstruction", "message_instruction")
                or ""
            ),
            "completionReport": (
                str(read_argument("completionReport", "completion_report") or "") if completed else ""
            ),
            "knownFacts": [
                str(item)
                for item in (read_argument("knownFacts", "known_facts", default=[]) or [])
                if str(item).strip()
            ],
            "pendingConditions": [
                str(item)
                for item in (read_argument("pendingConditions", "pending_conditions", default=[]) or [])
                if str(item).strip()
            ],
            "evidence": [
                str(item) for item in (read_argument("evidence", default=[]) or []) if str(item).strip()
            ],
            "evidenceEventIds": [
                str(item)
                for item in (read_argument("evidenceEventIds", "evidence_event_ids", default=[]) or [])
                if str(item).strip()
            ],
            "toolArguments": dict(arguments),
        }

    def _validate_completion_tool_call(
        self,
        evaluation: dict[str, Any],
        timeline: list[dict[str, Any]],
        event: dict[str, Any],
        task: dict[str, Any],
        previous_state: dict[str, Any],
    ) -> dict[str, Any]:
        """校验结束工具只能引用任务创建后的联系人证据，允许跨轮、跨天和重放场景完成。"""
        current_event_id = str(event.get("eventId") or "").strip()
        evidence_ids = [str(item) for item in (evaluation.get("evidenceEventIds") or []) if str(item).strip()]
        timeline_by_id = {
            str(item.get("eventId") or ""): item
            for item in timeline
            if str(item.get("eventId") or "").strip()
        }
        evidence_rows = [timeline_by_id.get(item) for item in evidence_ids]
        task_anchor = self._resolve_task_anchor(task, previous_state)
        # 结束判断必须基于时间线证据，而不是当前触发事件。
        # 原因：任务可能在跨天恢复、客户端重启、主动发信回写或批处理重放时才被最终判断完成；
        # 只要求“当前事件是联系人入站”会错误拦截这些合法完成场景。
        evidence_in_task_scope = all(
            row is not None
            and self._is_peer_timeline_row(row)
            and self._is_at_or_after_task_anchor(row.get("at"), task_anchor)
            for row in evidence_rows
        )
        # 旧任务若完全没有创建时间，就无法证明历史消息是否属于本任务；
        # 此时仍要求当前消息在证据中，作为兼容旧数据的保守边界。
        evidence_matches_legacy_fallback = bool(task_anchor) or (
            bool(current_event_id) and current_event_id in evidence_ids
        )
        evidence_is_valid = (
            bool(evidence_rows)
            and evidence_in_task_scope
            and evidence_matches_legacy_fallback
            and bool(str(evaluation.get("completionReport") or "").strip())
        )
        if evidence_is_valid:
            return evaluation
        return {
            **evaluation,
            "status": "ACTIVE",
            # 结束证据不足时只能保存等待状态，不能把“结束失败”误降级成再次发消息，
            # 否则模型会对同一轮联系人消息重复回复。
            "requestedTool": "update_delegated_task",
            "reason": "结束工具缺少任务创建后的可核验联系人证据，任务继续推进",
            "progressSummary": "完成条件尚未获得任务范围内的有效联系人证据",
            "messageInstruction": "",
            "completionReport": "",
            "evidence": [],
            "evidenceEventIds": [],
            "toolArguments": {},
        }

    def _finalize_runtime(self, state: RuntimeState) -> dict[str, Any]:
        """合并旧状态和本轮证据，生成提交给 Java 的幂等更新数据。"""
        runtime_input = state["runtime_input"]
        evaluation = state.get("evaluation") or {}
        previous = state.get("previous_state") or {}
        status = str(evaluation.get("status") or "ACTIVE").upper()
        if status not in {"ACTIVE", "COMPLETED", "FAILED"}:
            status = "ACTIVE"
        known_facts = self._merge_unique(previous.get("knownFacts"), evaluation.get("knownFacts"))
        pending = self._merge_unique(previous.get("pendingConditions"), evaluation.get("pendingConditions"))
        timeline = list(state.get("timeline") or [])[-500:]
        pre_task_history = self._normalize_pre_task_history(
            runtime_input.pre_task_history or previous.get("preTaskHistory")
        )
        history_access_allowed = bool(
            previous.get("historyAccessAllowed", runtime_input.history_access_allowed)
        )
        graph_state = {
            **previous,
            "graphVersion": 2,
            "taskCreatedAt": (
                str(previous.get("taskCreatedAt") or "")
                or self._task_created_at_text(runtime_input.task)
            ),
            "taskTimezone": str(previous.get("taskTimezone") or self._timezone_name()),
            "resolvedTimeText": self._resolved_task_time_text(
                runtime_input.task,
                previous,
                self._resolve_task_anchor(runtime_input.task, previous),
            ),
            "knownFacts": known_facts,
            "pendingConditions": [] if status != "ACTIVE" else pending,
            "workingMemory": self._build_working_memory(
                previous=previous,
                progress=str(evaluation.get("progressSummary") or "已处理最新消息，等待联系人进一步回应"),
                known_facts=known_facts,
                pending_conditions=[] if status != "ACTIVE" else pending,
                status=status,
                timeline=timeline,
            ),
            "preTaskHistory": pre_task_history,
            "historyAccessAllowed": history_access_allowed,
            # 保留最近的去重时间线，供重启恢复、任务详情和下一轮判断共同使用。
            "timeline": timeline,
            "lastEvaluatedAt": datetime.now(timezone.utc).isoformat(),
            "lastEvidence": list(evaluation.get("evidence") or [])[:10],
        }
        # 主控台创建任务后主动发送的首条消息必须持久化，避免 Python 或客户端重启后重复开场。
        if (
            str(runtime_input.event.get("eventType") or "").lower() == "delegated_task_started"
            and any(str(action).startswith("qq_write_back_sent:") for action in runtime_input.write_back_actions)
        ):
            graph_state["lastWriteBackStatus"] = "SENT"
            graph_state["lastWriteBackEventId"] = str(runtime_input.event.get("eventId") or "")
        result = DelegatedTaskRuntimeDecision(
            status=status,
            progressSummary=str(evaluation.get("progressSummary") or "已处理最新消息，等待对方进一步回应"),
            stateJson=json.dumps(graph_state, ensure_ascii=False),
            lastEventId=str(runtime_input.event.get("eventId") or ""),
            completionReport=str(evaluation.get("completionReport") or "") if status != "ACTIVE" else "",
            evidence=list(evaluation.get("evidence") or [])[:10],
            requestedTool="complete_delegated_task" if status == "COMPLETED" else "update_delegated_task",
            toolArguments={
                "status": status,
                "evidence": list(evaluation.get("evidence") or [])[:10],
            },
        )
        return {"result": result}

    def _fallback_intent(self, command: str, target_query: str) -> dict[str, Any]:
        """在模型不可用时提取最小可执行目标，避免生成未经用户表达的业务事实。"""
        deadline = ""
        deadline_match = self._DEADLINE_PATTERN.search(command)
        if deadline_match:
            deadline = deadline_match.group(0).strip()
        objective = re.sub(r"^(请|麻烦)?(帮我|替我|代我)", "", command).strip(" ，。") or command
        return {
            "taskType": "CONVERSATION_GOAL",
            "targetQuery": target_query,
            "targetChatType": self._infer_target_chat_type(command),
            "objective": objective,
            "successCriteria": "对方明确接受、拒绝或提出无法继续的条件",
            "deadlineText": deadline,
        }

    @staticmethod
    def _infer_target_chat_type(command: str) -> str:
        """根据明确的群聊措辞判断目标类型；面向某个人的委托默认只解析私聊。"""
        normalized = (command or "").lower()
        group_markers = ("群聊", "群里", "群中", "群内", "这个群", "群组")
        return "group" if any(marker in normalized for marker in group_markers) else "private"

    @staticmethod
    def _normalize_chat_type(chat_type: str) -> str:
        """统一不同连接器使用的会话类型名称，避免 friend/direct 与 private 无法匹配。"""
        normalized = (chat_type or "").strip().lower()
        if normalized in {"private", "friend", "direct", "dm"}:
            return "private"
        if normalized in {"group", "group_chat", "channel"}:
            return "group"
        return normalized

    @staticmethod
    def _parse_json_object(raw: str) -> dict[str, Any]:
        """从纯 JSON 或 Markdown 代码块中安全提取首个对象。"""
        text = (raw or "").strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
        try:
            value = json.loads(text)
            return value if isinstance(value, dict) else {}
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, flags=re.DOTALL)
            if not match:
                return {}
            try:
                value = json.loads(match.group(0))
                return value if isinstance(value, dict) else {}
            except json.JSONDecodeError:
                return {}

    @staticmethod
    def _safe_json(raw: Any) -> dict[str, Any]:
        """读取数据库中的图状态；损坏或旧版本内容统一按空状态处理。"""
        if isinstance(raw, dict):
            return raw
        try:
            value = json.loads(str(raw or "{}"))
            return value if isinstance(value, dict) else {}
        except json.JSONDecodeError:
            return {}

    @staticmethod
    def _merge_unique(left: Any, right: Any) -> list[str]:
        """按原顺序合并状态证据，避免每轮执行重复膨胀。"""
        result: list[str] = []
        for item in [*(left or []), *(right or [])]:
            text = str(item).strip()
            if text and text not in result:
                result.append(text)
        return result[-30:]

    @classmethod
    def _normalize_pre_task_history(cls, rows: Any) -> list[dict[str, Any]]:
        """清洗任务创建前的受控背景，确保它不能与任务内执行证据混用。"""
        deduplicated: dict[str, dict[str, Any]] = {}
        for raw in rows or []:
            if not isinstance(raw, dict):
                continue
            payload = raw.get("rawPayload") if isinstance(raw.get("rawPayload"), dict) else {}
            origin = str(raw.get("messageOrigin") or payload.get("messageOrigin") or "").upper()
            if origin == "INTERNAL":
                continue
            text = " ".join(str(raw.get("text") or payload.get("text") or "").split())
            if not text:
                continue
            event_id = str(raw.get("eventId") or raw.get("platformMessageId") or raw.get("clientMessageId") or "")
            at = str(raw.get("sentAt") or raw.get("timestamp") or raw.get("receivedAt") or raw.get("importedAt") or "")
            key = event_id or f"{at}:{text}"
            direction = str(raw.get("direction") or payload.get("direction") or "").upper()
            actor = str(raw.get("actorType") or payload.get("actorType") or "").upper()
            if actor == "OWNER" or (direction == "OUTBOUND" and origin != "AGENT"):
                speaker = "我"
            elif actor in {"AGENT", "SYSTEM"} or origin == "AGENT":
                speaker = "代理" if actor != "SYSTEM" else "系统"
            else:
                speaker = "对方"
            deduplicated[key] = {
                "eventId": event_id,
                "at": at,
                "speaker": speaker,
                "text": text,
                "eventType": str(raw.get("eventType") or "message").lower(),
            }
        return sorted(deduplicated.values(), key=lambda item: (item["at"], item["eventId"]))[-30:]

    @staticmethod
    def _build_working_memory(
        *,
        previous: dict[str, Any],
        progress: str,
        known_facts: list[str],
        pending_conditions: list[str],
        status: str,
        timeline: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """生成轻量工作记忆，供任务重启后的下一轮决策快速恢复当前阶段。"""
        old_memory = previous.get("workingMemory") if isinstance(previous.get("workingMemory"), dict) else {}
        latest_event_at = str(timeline[-1].get("at") or "") if timeline else str(old_memory.get("lastTimelineEventAt") or "")
        return {
            **old_memory,
            "phase": status,
            "summary": " ".join(str(progress or "").split()),
            "knownFacts": list(known_facts)[-15:],
            "pendingConditions": list(pending_conditions)[-15:],
            "lastTimelineEventAt": latest_event_at,
            "lastUpdatedAt": datetime.now(timezone.utc).isoformat(),
        }

    @staticmethod
    def _timezone_name() -> str:
        """返回委托任务解释自然语言时间所使用的 IANA 时区名称。"""
        return str(os.getenv("MEMO_ECHO_TIMEZONE") or "Asia/Shanghai").strip() or "Asia/Shanghai"

    @classmethod
    def _runtime_timezone(cls):
        """加载运行时区；系统缺少时区数据库时回退到中国标准时间。"""
        try:
            return ZoneInfo(cls._timezone_name())
        except (ZoneInfoNotFoundError, ValueError):
            return timezone(timedelta(hours=8), name="Asia/Shanghai")

    @classmethod
    def _now(cls) -> datetime:
        """返回带时区的当前时间，避免 naive datetime 在跨天任务中产生歧义。"""
        return datetime.now(cls._runtime_timezone())

    @classmethod
    def _parse_timestamp(cls, value: Any) -> datetime | None:
        """解析 Java、NapCat 和导入记录常见的 ISO 时间格式并统一附加时区。"""
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=cls._runtime_timezone())
        return parsed.astimezone(cls._runtime_timezone())

    @staticmethod
    def _task_created_at_text(task: dict[str, Any]) -> str:
        """从 Java 任务对象读取创建时间，兼容驼峰和下划线字段。"""
        return str(
            task.get("createdAt")
            or task.get("created_at")
            or task.get("taskCreatedAt")
            or ""
        ).strip()

    @classmethod
    def _resolve_task_anchor(
        cls,
        task: dict[str, Any],
        previous_state: dict[str, Any],
    ) -> datetime | None:
        """恢复任务创建锚点，确保跨重启后仍能区分任务前旧消息和任务内证据。"""
        direct_candidates = (
            previous_state.get("taskCreatedAt"),
            cls._task_created_at_text(task),
        )
        for candidate in direct_candidates:
            parsed = cls._parse_timestamp(candidate)
            if parsed is not None:
                return parsed
        for item in previous_state.get("timeline") or []:
            if not isinstance(item, dict) or str(item.get("type") or "") != "TASK_COMPILED":
                continue
            parsed = cls._parse_timestamp(item.get("at"))
            if parsed is not None:
                return parsed
        return None

    @classmethod
    def _resolved_task_time_text(
        cls,
        task: dict[str, Any],
        previous_state: dict[str, Any],
        task_anchor: datetime | None = None,
    ) -> str:
        """恢复任务的绝对时间口径，并兼容升级前未保存 ``resolvedTimeText`` 的活动任务。

        新任务会在编译阶段固化相对日期；旧任务可能只有 ``deadlineText`` 和数据库创建时间。
        此处只能按任务创建时间补算一次，不能按当前时间重算，否则跨天后“明天”会不断漂移。
        """
        stored = str(previous_state.get("resolvedTimeText") or "").strip()
        if stored:
            return stored
        deadline_text = str(
            task.get("deadlineText")
            or task.get("deadline_text")
            or ""
        ).strip()
        if not deadline_text:
            return ""
        anchor = task_anchor or cls._resolve_task_anchor(task, previous_state)
        if anchor is None:
            return deadline_text
        return cls._resolve_relative_deadline(deadline_text, anchor)

    @classmethod
    def _is_at_or_after_task_anchor(cls, value: Any, anchor: datetime | None) -> bool:
        """判断证据是否发生在任务创建之后；无锚点时交给旧数据兼容分支处理。"""
        if anchor is None:
            return True
        occurred_at = cls._parse_timestamp(value)
        return occurred_at is not None and occurred_at >= anchor

    @classmethod
    def _resolve_relative_deadline(cls, deadline_text: str, created_at: datetime) -> str:
        """在任务创建时把常见相对日期固化为绝对日期，后续只复用结果而不重复推算。"""
        text = str(deadline_text or "").strip()
        if not text:
            return ""
        local_created_at = created_at.astimezone(cls._runtime_timezone())
        replacements = (
            ("后天", 2, ""),
            ("明晚", 1, "晚上"),
            ("明早", 1, "早上"),
            ("明天", 1, ""),
            ("今晚", 0, "晚上"),
            ("今天", 0, ""),
        )
        for marker, offset, period in replacements:
            if marker not in text:
                continue
            target_date = (local_created_at + timedelta(days=offset)).date().isoformat()
            resolved_marker = f"{target_date}{period}"
            return f"{text.replace(marker, resolved_marker, 1)}（任务创建时解析）"
        return text
