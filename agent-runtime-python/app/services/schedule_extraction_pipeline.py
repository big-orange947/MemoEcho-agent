from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from app.agents.schedule_extractor import ScheduleCandidate, ScheduleExtractor
from app.clients.llm_service import LlmServiceClient
from app.schemas.model_profiles import ResolvedUserModelProfile
from app.schemas.schedules import (
    ScheduleCandidateStatus,
    ScheduleIntent,
    StructuredScheduleExtraction,
    StructuredScheduleItem,
)
from app.schemas.tasks import AgentTaskContext


logger = logging.getLogger(__name__)


@dataclass
class ScheduleExtractionOutcome:
    """汇总日程候选、意图、证据和校验状态，作为 Agent 与持久化层之间的安全边界。"""

    candidate: ScheduleCandidate
    intent: ScheduleIntent
    status: ScheduleCandidateStatus
    confidence: float
    source: str
    evidence: list[str] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)
    validation_errors: list[str] = field(default_factory=list)
    llm_used: bool = False
    raw_extraction: dict[str, Any] | None = None

    def can_persist(self) -> bool:
        # 这个函数的作用是把允许落库的条件收口到一个位置，避免 Agent 只检查 start_time 就直接写入。
        return (
            self.intent == ScheduleIntent.CREATE
            and self.status == ScheduleCandidateStatus.CONFIRMED
            and bool(self.candidate.start_time)
        )

    def to_dict(self) -> dict[str, Any]:
        # 这个函数的作用是输出可供前端诊断和测试断言使用的结构化管线结果。
        return {
            "intent": self.intent.value,
            "candidateStatus": self.status.value,
            "pipelineConfidence": round(self.confidence, 4),
            "extractionSource": self.source,
            "evidence": list(self.evidence),
            "missingFields": list(self.missing_fields),
            "validationErrors": list(self.validation_errors),
            "structuredExtractionUsed": self.llm_used,
            "rawStructuredExtraction": self.raw_extraction,
        }


class ScheduleExtractionPipeline:
    """组合规则解析、结构化 LLM 抽取和确定性校验，输出可执行或待确认的日程候选。"""

    CREATE_HINTS = (
        "会议", "开会", "例会", "举办", "举行", "活动", "路演", "讲座", "考试", "上课",
        "面试", "集合", "见面", "出发", "截止", "到期", "提交", "汇报", "沟通", "提醒我",
        "帮我记", "记一下", "别忘了", "安排", "预约",
        "meeting", "deadline", "remind", "appointment", "lecture", "exam", "submit",
    )
    QUERY_HINTS = (
        "有什么日程", "有哪些日程", "什么安排", "哪些安排", "日程吗", "安排吗", "几点",
        "什么时候", "列出日程", "查看日程", "最近的日程", "今天有空吗", "明天有空吗",
        "what schedule", "what is on my calendar", "list my schedule", "any meetings",
    )
    CANCEL_HINTS = (
        "取消", "删除日程", "删掉提醒", "不用提醒", "不需要提醒", "不参加",
        "cancel", "delete reminder", "remove reminder",
    )
    UPDATE_HINTS = ("改期", "改到", "改成", "推迟到", "提前到", "reschedule", "move the meeting")

    def __init__(
        self,
        extractor: ScheduleExtractor | None = None,
        llm_client: LlmServiceClient | None = None,
    ) -> None:
        # 这个构造函数的作用是注入本地提取器和可选 LLM；没有模型时仍能完成明确日程的安全抽取。
        self.extractor = extractor or ScheduleExtractor()
        self.llm_client = llm_client

    async def extract(
        self,
        task_context: AgentTaskContext,
        model_profile: ResolvedUserModelProfile | None,
    ) -> ScheduleExtractionOutcome:
        # 这个函数的作用是运行完整抽取管线：先做确定性解析，再按需调用 LLM，最后统一校验。
        text = self._normalize_text(task_context.event.text or "")
        reference = self._resolve_event_time(task_context.event.timestamp)
        rule_candidate = self.extractor.extract(text, now=reference)
        rule_intent = self._detect_rule_intent(text, rule_candidate)
        rule_intent = self._merge_semantic_create_evidence(task_context, rule_candidate, rule_intent)

        if self._is_strong_rule_candidate(rule_intent, rule_candidate):
            return self._validate_outcome(
                candidate=rule_candidate,
                intent=rule_intent,
                confidence=0.94,
                source="deterministic_rule",
                evidence=rule_candidate.evidence,
                reference=reference,
            )

        structured = await self._extract_with_llm(task_context, model_profile, reference)
        if structured is not None:
            outcome = self._build_llm_outcome(
                task_context,
                rule_candidate,
                structured,
                reference,
            )
            if outcome is not None:
                return outcome

        fallback_confidence = 0.72 if rule_candidate.start_time else 0.45
        return self._validate_outcome(
            candidate=rule_candidate,
            intent=rule_intent,
            confidence=fallback_confidence,
            source="rule_fallback",
            evidence=rule_candidate.evidence,
            reference=reference,
        )

    @staticmethod
    def _merge_semantic_create_evidence(
        task_context: AgentTaskContext,
        candidate: ScheduleCandidate,
        rule_intent: ScheduleIntent,
    ) -> ScheduleIntent:
        # 这个函数的作用是把编排层已确认的创建语义与规则层日期时间证据合并，处理“设备测试”等开放活动名称。
        # 查询、取消和改期意图拥有更高优先级，任何语义门控结果都不能覆盖这些明确否定证据。
        if rule_intent not in {ScheduleIntent.NONE, ScheduleIntent.AMBIGUOUS}:
            return rule_intent
        semantic_decision = task_context.metadata.get("semantic_schedule_intent") or {}
        if not isinstance(semantic_decision, dict):
            return rule_intent
        semantic_create = (
            semantic_decision.get("label") == "schedule_create"
            and semantic_decision.get("route") == "schedule_extract"
            and bool(semantic_decision.get("decisive"))
        )
        has_structured_evidence = bool(
            candidate.start_time
            and candidate.date_is_explicit
            and candidate.time_is_explicit
            and not candidate.ambiguous
        )
        return ScheduleIntent.CREATE if semantic_create and has_structured_evidence else rule_intent

    @staticmethod
    def _normalize_text(text: str) -> str:
        # 这个函数的作用是压平平台消息中的重复空白，保留原始语义和可回溯证据片段。
        return re.sub(r"\s+", " ", text or "").strip()

    def _detect_rule_intent(self, text: str, candidate: ScheduleCandidate) -> ScheduleIntent:
        # 这个函数的作用是用高精度规则先排除查询、取消和否定表达，减少不必要的模型调用与误落库。
        lowered_text = text.lower()
        if any(hint in lowered_text for hint in self.CANCEL_HINTS):
            return ScheduleIntent.CANCEL
        if any(hint in lowered_text for hint in self.UPDATE_HINTS):
            return ScheduleIntent.UPDATE
        if any(hint in lowered_text for hint in self.QUERY_HINTS):
            return ScheduleIntent.QUERY
        if re.search(r"(?:日程|安排|会议|提醒).{0,8}[吗么？?]$", text):
            return ScheduleIntent.QUERY
        if candidate.start_time and (
            any(hint in lowered_text for hint in self.CREATE_HINTS)
            or bool(re.search(r"(?:去|到|和|跟).{0,20}(?:见|聊|吃饭|训练|学习)", text))
        ):
            return ScheduleIntent.CREATE
        return ScheduleIntent.AMBIGUOUS if candidate.start_time else ScheduleIntent.NONE

    @staticmethod
    def _is_strong_rule_candidate(intent: ScheduleIntent, candidate: ScheduleCandidate) -> bool:
        # 这个函数的作用是识别无需 LLM 也能确定的日程，降低成本并避免模型改写明确时间事实。
        return (
            intent == ScheduleIntent.CREATE
            and bool(candidate.start_time)
            and candidate.time_is_explicit
            and candidate.date_is_explicit
            and not candidate.ambiguous
        )

    async def _extract_with_llm(
        self,
        task_context: AgentTaskContext,
        model_profile: ResolvedUserModelProfile | None,
        reference: datetime,
    ) -> StructuredScheduleExtraction | None:
        # 这个函数的作用是在规则不足时要求模型只返回结构化候选；调用或解析失败时静默降级到本地规则。
        if self.llm_client is None or not self.llm_client.is_enabled(model_profile):
            return None
        try:
            response = await self.llm_client.generate_reply(
                self._build_structured_system_prompt(),
                self._build_structured_user_message(task_context, reference),
                temperature=0.1,
                model_profile=model_profile,
            )
            payload = self._parse_json_object(response)
            normalized_payload = self._normalize_structured_payload(payload)
            return StructuredScheduleExtraction.model_validate(normalized_payload)
        except Exception as exception:
            logger.info("结构化日程抽取失败，已回退本地规则。eventId=%s, error=%s", task_context.event.event_id, exception)
            return None

    @staticmethod
    def _build_structured_system_prompt() -> str:
        # 这个函数的作用是限定模型只做意图和字段抽取，不允许自行决定落库或补造消息中不存在的事实。
        return (
            "你是 Memo Echo 的结构化日程抽取器，不是聊天助手。\n"
            "只依据当前消息、给出的近期上下文和参考时间提取事实，不得使用常识补造日期、地点、参与人或事件。\n"
            "先区分 CREATE、UPDATE、CANCEL、QUERY、NONE、AMBIGUOUS；查询日程绝不能标为 CREATE。\n"
            "每个候选必须在 evidence 中逐字引用支持它的原文片段。没有证据的字段必须留空。\n"
            "normalizedStartTime 和 normalizedEndTime 使用 ISO 8601；无法唯一确定时留空并写入 missingFields。\n"
            "只输出一个 JSON 对象，不使用 Markdown，不输出解释。"
        )

    def _build_structured_user_message(self, task_context: AgentTaskContext, reference: datetime) -> str:
        # 这个函数的作用是向模型提供参考时间、当前消息和有限历史，并附上严格 JSON 字段契约。
        history = self._serialize_history(task_context.history_context)
        schema_example = {
            "intent": "CREATE",
            "negated": False,
            "confidence": 0.0,
            "events": [
                {
                    "title": "",
                    "dateText": "",
                    "startTimeText": "",
                    "endTimeText": "",
                    "normalizedStartTime": "",
                    "normalizedEndTime": "",
                    "location": "",
                    "participants": [],
                    "evidence": [],
                    "missingFields": [],
                    "confidence": 0.0,
                }
            ],
        }
        return (
            f"参考时间：{reference.isoformat()}\n"
            f"平台：{task_context.event.platform}\n"
            f"会话类型：{task_context.event.chat_type}\n"
            f"当前消息：{task_context.event.text or ''}\n"
            f"近期上下文：{history}\n"
            f"输出结构：{json.dumps(schema_example, ensure_ascii=False)}"
        )

    @staticmethod
    def _serialize_history(history_context: list[dict[str, Any]]) -> str:
        # 这个函数的作用是限制历史上下文长度，既支持连续对话又避免把整段聊天无限塞入抽取请求。
        if not history_context:
            return "[]"
        compact_history = history_context[-12:]
        serialized = json.dumps(compact_history, ensure_ascii=False, default=str)
        return serialized[-6000:]

    @staticmethod
    def _parse_json_object(raw_response: str) -> dict[str, Any]:
        # 这个函数的作用是兼容纯 JSON 和被代码围栏包裹的模型输出，并拒绝没有合法对象的回复。
        normalized = str(raw_response or "").strip()
        normalized = re.sub(r"^```(?:json)?\s*", "", normalized, flags=re.IGNORECASE)
        normalized = re.sub(r"\s*```$", "", normalized)
        try:
            payload = json.loads(normalized)
            if isinstance(payload, dict):
                return payload
        except json.JSONDecodeError:
            pass

        start_index = normalized.find("{")
        if start_index >= 0:
            payload, _ = json.JSONDecoder().raw_decode(normalized[start_index:])
            if isinstance(payload, dict):
                return payload
        raise ValueError("LLM did not return a JSON object")

    @staticmethod
    def _normalize_structured_payload(payload: dict[str, Any]) -> dict[str, Any]:
        # 这个函数的作用是兼容模型返回的小写意图和轻微字段偏差，再交给 Pydantic 做严格类型校验。
        normalized = dict(payload)
        normalized["intent"] = str(normalized.get("intent") or "NONE").strip().upper()
        events = normalized.get("events")
        normalized["events"] = events if isinstance(events, list) else []
        return normalized

    def _build_llm_outcome(
        self,
        task_context: AgentTaskContext,
        rule_candidate: ScheduleCandidate,
        structured: StructuredScheduleExtraction,
        reference: datetime,
    ) -> ScheduleExtractionOutcome | None:
        # 这个函数的作用是把模型 JSON 转为标准候选，并验证每个可变字段是否能在聊天证据中找到来源。
        if structured.intent not in {ScheduleIntent.CREATE, ScheduleIntent.UPDATE}:
            return self._validate_outcome(
                candidate=rule_candidate,
                intent=structured.intent,
                confidence=structured.confidence,
                source="llm_structured",
                evidence=[],
                reference=reference,
                llm_used=True,
                raw_extraction=structured.model_dump(by_alias=True),
            )
        if not structured.events:
            return None

        item = structured.events[0]
        evidence_corpus = self._build_evidence_corpus(task_context)
        verified_evidence = [
            evidence.strip()
            for evidence in item.evidence
            if evidence.strip() and evidence.strip() in evidence_corpus
        ]
        time_evidence = "".join((item.date_text, item.start_time_text)).strip()
        time_is_supported = bool(
            item.start_time_text.strip()
            and item.start_time_text.strip() in evidence_corpus
            and (not item.date_text.strip() or item.date_text.strip() in evidence_corpus)
        )

        start_time = rule_candidate.start_time
        end_time = rule_candidate.end_time
        if not start_time and time_is_supported:
            start_time = self._normalize_model_datetime(item.normalized_start_time, reference)
            end_time = self._normalize_model_datetime(item.normalized_end_time, reference)

        title = item.title.strip() or rule_candidate.title
        location = rule_candidate.location
        if not location and item.location.strip() and item.location.strip() in evidence_corpus:
            location = item.location.strip()
        participants = rule_candidate.participants
        if not participants:
            verified_participants = [name.strip() for name in item.participants if name.strip() in evidence_corpus]
            participants = "、".join(verified_participants) or None

        candidate = ScheduleCandidate(
            title=title,
            start_time=start_time,
            end_time=end_time,
            location=location,
            content=rule_candidate.content,
            participants=participants,
            confidence=self._confidence_label(min(item.confidence, structured.confidence)),
            evidence=list(dict.fromkeys([*rule_candidate.evidence, *verified_evidence, time_evidence])),
            date_is_explicit=rule_candidate.date_is_explicit or bool(item.date_text.strip()),
            time_is_explicit=rule_candidate.time_is_explicit or time_is_supported,
            ambiguous=rule_candidate.ambiguous,
        )
        additional_errors = []
        if len(structured.events) > 1:
            additional_errors.append("multiple_events_require_separate_confirmation")
        if structured.negated:
            additional_errors.append("negated_schedule_intent")
        if item.normalized_start_time and not start_time:
            additional_errors.append("normalized_time_has_no_source_evidence")

        return self._validate_outcome(
            candidate=candidate,
            intent=structured.intent,
            confidence=min(item.confidence, structured.confidence),
            source="llm_structured",
            evidence=candidate.evidence,
            reference=reference,
            llm_used=True,
            raw_extraction=structured.model_dump(by_alias=True),
            additional_errors=additional_errors,
        )

    @staticmethod
    def _build_evidence_corpus(task_context: AgentTaskContext) -> str:
        # 这个函数的作用是把当前消息和近期历史合并成只读证据库，模型输出的字段必须能在其中回溯。
        history = json.dumps(task_context.history_context[-12:], ensure_ascii=False, default=str)
        return f"{task_context.event.text or ''}\n{history}"

    @staticmethod
    def _normalize_model_datetime(value: str, reference: datetime) -> str | None:
        # 这个函数的作用是校验模型给出的 ISO 时间，并统一转换为日程服务当前使用的无时区格式。
        normalized = str(value or "").strip()
        if not normalized:
            return None
        if normalized.endswith("Z"):
            normalized = f"{normalized[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return None
        if parsed.tzinfo is not None and reference.tzinfo is not None:
            parsed = parsed.astimezone(reference.tzinfo)
        return parsed.replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")

    def _validate_outcome(
        self,
        candidate: ScheduleCandidate,
        intent: ScheduleIntent,
        confidence: float,
        source: str,
        evidence: list[str],
        reference: datetime,
        llm_used: bool = False,
        raw_extraction: dict[str, Any] | None = None,
        additional_errors: list[str] | None = None,
    ) -> ScheduleExtractionOutcome:
        # 这个函数的作用是执行最终确定性校验，并将不安全候选降级为草稿、待澄清或拒绝状态。
        errors = list(additional_errors or [])
        missing_fields: list[str] = []
        if intent in {ScheduleIntent.NONE, ScheduleIntent.QUERY, ScheduleIntent.CANCEL}:
            status = ScheduleCandidateStatus.REJECTED
            errors.append(f"intent_{intent.value.lower()}_cannot_create_schedule")
        elif intent == ScheduleIntent.UPDATE:
            status = ScheduleCandidateStatus.DRAFT
            errors.append("schedule_update_tool_not_available")
        elif not candidate.start_time:
            status = ScheduleCandidateStatus.NEEDS_CLARIFICATION
            missing_fields.append("start_time")
        elif candidate.ambiguous:
            status = ScheduleCandidateStatus.NEEDS_CLARIFICATION
            errors.append("ambiguous_time_expression")
        else:
            status = ScheduleCandidateStatus.CONFIRMED

        start_dt = self._parse_service_datetime(candidate.start_time)
        end_dt = self._parse_service_datetime(candidate.end_time)
        if start_dt and end_dt and end_dt <= start_dt:
            status = ScheduleCandidateStatus.NEEDS_CLARIFICATION
            errors.append("end_time_must_be_after_start_time")
        reference_without_tz = reference.replace(tzinfo=None)
        if start_dt and start_dt < reference_without_tz - timedelta(minutes=5):
            status = ScheduleCandidateStatus.DRAFT
            errors.append("start_time_is_in_the_past")
        if not evidence and status == ScheduleCandidateStatus.CONFIRMED:
            status = ScheduleCandidateStatus.DRAFT
            errors.append("source_evidence_missing")
        if confidence < 0.65 and status == ScheduleCandidateStatus.CONFIRMED:
            status = ScheduleCandidateStatus.NEEDS_CLARIFICATION
            errors.append("confidence_below_auto_persist_threshold")
        if errors and status == ScheduleCandidateStatus.CONFIRMED:
            status = ScheduleCandidateStatus.DRAFT

        candidate.confidence = self._confidence_label(confidence)
        return ScheduleExtractionOutcome(
            candidate=candidate,
            intent=intent,
            status=status,
            confidence=max(0.0, min(float(confidence), 1.0)),
            source=source,
            evidence=list(dict.fromkeys(item for item in evidence if item)),
            missing_fields=missing_fields,
            validation_errors=list(dict.fromkeys(errors)),
            llm_used=llm_used,
            raw_extraction=raw_extraction,
        )

    @staticmethod
    def _parse_service_datetime(value: str | None) -> datetime | None:
        # 这个函数的作用是把标准服务时间还原为 datetime，供过去时间和先后顺序校验使用。
        if not value:
            return None
        try:
            return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None

    @staticmethod
    def _confidence_label(confidence: float) -> str:
        # 这个函数的作用是把连续置信度映射到现有 Java 服务兼容的 high、medium、low 字符串。
        if confidence >= 0.82:
            return "high"
        if confidence >= 0.6:
            return "medium"
        return "low"

    @staticmethod
    def _resolve_event_time(timestamp: str) -> datetime:
        # 这个函数的作用是用事件发生时刻解析相对日期；格式异常时才回退到运行时当前时间。
        normalized = str(timestamp or "").strip()
        if normalized.endswith("Z"):
            normalized = f"{normalized[:-1]}+00:00"
        try:
            return datetime.fromisoformat(normalized) if normalized else datetime.now().astimezone()
        except ValueError:
            return datetime.now().astimezone()
