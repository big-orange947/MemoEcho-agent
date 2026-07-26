from __future__ import annotations

import json
import logging
import re
from difflib import SequenceMatcher
from typing import Any

from app.agents.base import BaseAgent
from app.clients.llm_service import LlmServiceClient
from app.clients.public_knowledge_search import PublicKnowledgeSearchClient
from app.schemas.results import AgentResult
from app.schemas.tasks import AgentTaskContext
from app.services.delegated_task_evidence import DelegatedTaskEvidenceBuilder
from app.services.skill_review_evidence import SkillReviewEvidenceBuilder


logger = logging.getLogger(__name__)


class ContextReviewAgent(BaseAgent):
    """检查候选回复是否符合当前话题、人设、说话人身份和公共世界知识。"""

    name = "context_review"

    def __init__(
        self,
        tools,
        llm_client: LlmServiceClient | None = None,
        search_client: PublicKnowledgeSearchClient | None = None,
    ) -> None:
        """注入生成模型和受限检索客户端；检索能力不直接暴露给 SocialAgent。"""
        super().__init__(tools)
        self.llm_client = llm_client
        self.search_client = search_client or PublicKnowledgeSearchClient()

    async def run(self, task_context: AgentTaskContext, action: str) -> AgentResult:
        """
        审查 SocialAgent 草稿，必要时执行一次实体检索并重新审查。

        这里不负责账号、付款和现实操作授权；这些仍由最后的 ReviewAgent 闭世界审查。
        分层后，情景错误不会被大量业务关键词特判掩盖，最终发送闸门也不会被绕过。
        """
        previous = task_context.metadata.get("previous_results") or {}
        social = previous.get("social") or {}
        candidate = str(social.get("draft") or social.get("proposedDraft") or "").strip()
        original_candidate = candidate
        profile = self._extract_profile(task_context)
        delegated_task = (task_context.metadata or {}).get("delegated_task")
        # 主控台委托允许在任务范围内持续协商；情景问题先改写，不能直接套用设定集的严格接管默认值。
        review_mode = DelegatedTaskEvidenceBuilder.resolve_review_mode(profile, delegated_task)
        if social.get("handoffRequired"):
            return self._handoff(
                task_context,
                candidate,
                str(social.get("handoffReason") or "上游生成阶段要求人工接管"),
            )
        if not candidate:
            return self._handoff(task_context, candidate, "没有可进行情景审查的候选回复")

        model_profile = self._extract_model_profile(task_context)
        if self.llm_client is None or not self.llm_client.is_enabled(model_profile):
            # 情景审查本身就是发送闸门，模型不可用时不能伪装成已经通过。
            return self._handoff(
                task_context,
                candidate,
                "情景审查模型不可用，无法确认草稿是否符合当前话题",
                assessment={"checks": {"reviewSkipped": True}},
            )

        # 先做不依赖模型的重复问题检查。这里只删除“历史里我问过，且之后对方已经回答”的气泡，
        # 不解析分数、省份等业务字段，因此同一机制也适用于问诊、售前、访谈等其他 Skill。
        candidate, repeated_questions = self._remove_answered_repeated_questions(
            candidate,
            task_context.history_context,
            task_context.event.text or "",
        )
        unresolved_repeated_questions: list[str] = []
        if not candidate:
            # 所有气泡都重复时保留原草稿给审查模型改写，不能直接生成一个脱离上下文的固定兜底。
            candidate = original_candidate
            unresolved_repeated_questions = repeated_questions
        prompt_evidence = self._build_prompt_evidence(task_context, profile)
        public_knowledge: list[dict[str, Any]] = []
        try:
            assessment = await self._review_with_model(
                task_context,
                candidate,
                prompt_evidence,
                review_mode,
                public_knowledge,
                allow_retrieve=True,
                model_profile=model_profile,
                repeated_questions=repeated_questions,
            )
            if assessment["decision"] == "RETRIEVE":
                public_knowledge = await self._retrieve_public_knowledge(profile, assessment)
                if public_knowledge:
                    assessment = await self._review_with_model(
                        task_context,
                        candidate,
                        prompt_evidence,
                        review_mode,
                        public_knowledge,
                        allow_retrieve=False,
                        model_profile=model_profile,
                        repeated_questions=repeated_questions,
                    )
                else:
                    assessment = self._resolve_unavailable_search(assessment, review_mode)
        except Exception as exception:
            # 两个审批 Agent 可能使用同一模型配置，不能把本层异常静默交给下一层后默认放行。
            transient_failure = LlmServiceClient.is_transient_error(exception)
            reason = (
                "情景审查服务暂时不可用：重试后仍发生网络超时，未发送未经审查的回复"
                if transient_failure
                else f"情景审查服务异常：{type(exception).__name__}"
            )
            return self._handoff(
                task_context,
                candidate,
                reason,
                assessment={
                    "checks": {
                        "reviewSkipped": True,
                        "reviewUnavailable": transient_failure,
                        "errorType": type(exception).__name__,
                    }
                },
            )

        assessment = self._enforce_review_contract(
            assessment,
            review_mode,
            unresolved_repeated_questions,
        )
        decision = assessment["decision"]
        reason = assessment["reason"]
        logger.info(
            "情景审查完成：eventId=%s, decision=%s, failedChecks=%s, unsupportedClaims=%d, conflicts=%d",
            task_context.event.event_id,
            decision,
            assessment.get("failedChecks") or [],
            len(assessment.get("unsupportedPersonalClaims") or []),
            len(assessment.get("entityConflicts") or []),
        )
        if decision == "HANDOFF":
            if review_mode == "AUTO_REWRITE":
                # 自动纠偏模式下，情景层只提供失败依据，不得提前创建人工接管事项。
                # 最终 ReviewAgent 会使用这些依据执行最多三轮“改写 -> 复审”。
                assessment = dict(assessment)
                assessment["decision"] = "REWRITE"
                decision = "REWRITE"
            else:
                return self._handoff(
                    task_context,
                    candidate,
                    reason or "当前情景缺少继续回复所需的用户事实",
                    assessment=assessment,
                    public_knowledge=public_knowledge,
                )

        reviewed_draft = candidate
        if decision == "REWRITE":
            # 情景模型能给出安全改写时优先使用；没有给出时保留原候选交给最终纠偏器处理。
            reviewed_draft = str(assessment.get("rewrittenDraft") or "").strip() or candidate
        return self._approved_result(
            task_context,
            reviewed_draft,
            decision=decision,
            reason=reason or "候选回复符合当前情景和人设",
            checks=assessment.get("checks") or {},
            original_draft=original_candidate,
            assessment=assessment,
            public_knowledge=public_knowledge,
        )

    async def _review_with_model(
        self,
        task_context: AgentTaskContext,
        candidate: str,
        prompt_evidence: str,
        review_mode: str,
        public_knowledge: list[dict[str, Any]],
        allow_retrieve: bool,
        model_profile,
        repeated_questions: list[str] | None = None,
    ) -> dict[str, Any]:
        """让独立模型按固定 JSON 契约完成多维情景审批，不直接生成新事实。"""
        history = self._format_history(task_context.history_context)
        public_evidence = self._format_public_knowledge(public_knowledge)
        repeated_evidence = " / ".join(repeated_questions or [])
        retrieve_rule = (
            "若唯一障碍是可公开验证的作品、人物、术语或产品事实，返回 RETRIEVE，"
            "并给出最多两个只含实体与关系的 searchQueries；不得复制聊天原文或个人信息。"
            if allow_retrieve
            else "本轮已经提供公共检索结果，不得再次返回 RETRIEVE；资料仍不足时安全改写或 HANDOFF。"
        )
        response = await self.llm_client.generate_reply(
            system_prompt=(
                "你是 ContextReviewAgent，是社交回复发送前的情景一致性审批层，不负责代替用户聊天。\n"
                "请分别检查：当前话题是否接得上、我和对方的身份是否混淆、候选是否符合会话人设、"
                "作品/人物/产品等实体是否属于正确世界、回复是否像自然即时聊天。\n"
                "历史中的‘我’始终是当前账号主人，‘对方’始终是聊天联系人；候选回复只能代表‘我’。\n"
                "历史只描述过去。除非证据明确说明仍在持续，否则不得把旧状态写成账号主人此刻正在做的事。\n"
                "候选声称‘我抽到了、我买了、我正在做、我认识、我拥有’等个人经历或当前状态时，"
                "必须能从历史、会话提示词或 Skill 直接找到依据；公共网页永远不能证明用户的私人经历。\n"
                "工作台中的活动委托契约是账号主人本轮明确给出的控制指令。契约里的目标、时间要求和普通任务参数"
                "属于可直接使用的高权威依据，不要求它们必须先出现在联系人聊天历史中；但契约不能证明联系人已经接受。\n"
                "不得输出括号动作、舞台说明、内心独白、客服腔，也不得突然引入当前话题之外的新实体。\n"
                "候选不得重复询问历史中已经问过、且对方随后已经回答的问题；遇到这种情况必须返回 REWRITE，"
                "删除重复项并只推进当前仍缺少的信息。\n"
                "当前消息若只是表情包、贴纸或动画表情，一个不含事实陈述和追问的简短情绪回应应直接 APPROVE；"
                "不得将它改写成询问出处、作者、模组、作品名称或其他新话题。\n"
                "APPROVE 表示无需修改；REWRITE 表示不新增事实即可改成自然且连贯的短回复；"
                "HANDOFF 只用于缺少用户私人事实、决定或授权且无法通过安全改写继续的情况。\n"
                f"{retrieve_rule}\n"
                f"当前最终审批策略为 {review_mode}。AUTO_REWRITE 时优先删除无依据内容并安全改写，"
                "但不能为了避免接管而编造事实。\n"
                "只输出 JSON，不要 Markdown："
                '{"decision":"APPROVE|REWRITE|RETRIEVE|HANDOFF","reason":"原因",'
                '"rewrittenDraft":"仅 REWRITE 或 RETRIEVE 的安全备选草稿",'
                '"searchQueries":["实体 关系"],'
                '"checks":{"contextCoherent":true,"personaAligned":true,'
                '"speakerConsistent":true,"worldKnowledgeConsistent":true,"naturalConversation":true,'
                '"answersLatestMessage":true,"currentStateGrounded":true,"doesNotRepeatAnsweredQuestion":true},'
                '"unsupportedPersonalClaims":[],"entityConflicts":[]}'
            ),
            user_message=(
                f"[会话提示词与 Skill]\n{prompt_evidence or '无'}\n"
                f"[近期历史，按时间从旧到新]\n{history or '无'}\n"
                f"[联系人当前最新消息]\n{(task_context.event.text or '').strip() or '[非文本消息]'}\n"
                f"[候选回复，代表我]\n{candidate}\n"
                f"[代码层重复问题信号]\n{repeated_evidence or '未发现'}\n"
                f"[受限公共检索结果]\n{public_evidence or '无'}"
            ),
            temperature=0.0,
            model_profile=model_profile,
        )
        return self._parse_assessment(response)

    async def _retrieve_public_knowledge(
        self,
        profile: dict[str, Any],
        assessment: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """只在用户未关闭公共检索且搜索客户端已配置时执行一次实体查询。"""
        if profile.get("publicKnowledgeSearchEnabled", True) is False:
            return []
        queries = assessment.get("searchQueries") or []
        if not isinstance(queries, list):
            return []
        return await self.search_client.search([str(query) for query in queries])

    @staticmethod
    def _resolve_unavailable_search(assessment: dict[str, Any], review_mode: str) -> dict[str, Any]:
        """检索未配置或无结果时，优先使用模型给出的无事实安全改写，否则请求人工接管。"""
        fallback = str(assessment.get("rewrittenDraft") or "").strip()
        resolved = dict(assessment)
        if review_mode == "AUTO_REWRITE":
            resolved["decision"] = "REWRITE"
            resolved["reason"] = str(resolved.get("reason") or "公共资料不足，使用无事实安全改写")
            return resolved
        resolved["decision"] = "HANDOFF"
        resolved["reason"] = str(resolved.get("reason") or "公共事实存在冲突且当前未配置检索服务")
        return resolved

    def _build_prompt_evidence(self, task_context: AgentTaskContext, profile: dict[str, Any]) -> str:
        """合并会话设定、紧凑 Skill 证据和知识库，控制独立审查的输入规模。"""
        fragments: list[str] = []
        profile_prompt = str(profile.get("systemPrompt") or "").strip()
        if profile_prompt:
            fragments.append(
                "[用户会话设定]\n"
                + SkillReviewEvidenceBuilder.compact_text(profile_prompt, 2000)
            )
        skill_evidence = SkillReviewEvidenceBuilder.build(
            self._extract_resolved_skills(task_context)
        )
        if skill_evidence:
            fragments.append(skill_evidence)
        delegated_evidence = DelegatedTaskEvidenceBuilder.build(
            (task_context.metadata or {}).get("delegated_task"),
            (task_context.metadata or {}).get("delegated_task_action"),
        )
        if delegated_evidence:
            fragments.append(delegated_evidence)
        for item in task_context.retrieved_knowledge[:3]:
            source = str(item.get("source") or "").strip()
            content = " ".join(str(item.get("content") or "").split()).strip()
            if content:
                fragments.append(
                    f"[用户知识库: {source or '未命名来源'}]\n"
                    + SkillReviewEvidenceBuilder.compact_text(content, 700)
                )
        return "\n\n".join(fragments)

    @staticmethod
    def _format_history(history: list[dict[str, Any]]) -> str:
        """同时标注说话方、时间和代理来源，代理旧草稿不能成为用户事实依据。"""
        lines: list[str] = []
        for item in history[-16:]:
            text = " ".join(str(item.get("text") or "").split()).strip()
            if not text:
                continue
            origin = str(item.get("messageOrigin") or "EXTERNAL").upper()
            authority = str(item.get("factAuthority") or "")
            if authority == "derived_summary" or bool(item.get("derivedSummary")):
                speaker = "较早对话派生摘录（低权威，不可单独证明事实）"
            elif authority == "agent_output" or origin in {"AGENT_AUTO", "AGENT_CONFIRMED"}:
                speaker = "代理曾以我的账号发送（仅供衔接，不能证明用户事实或当前状态）"
            else:
                speaker = "我" if str(item.get("role") or "") == "self" else "对方"
            timestamp = str(item.get("timestamp") or "").strip()
            time_label = f"[{timestamp}] " if timestamp else ""
            lines.append(f"{time_label}{speaker}：{text}")
        return "\n".join(lines)

    @staticmethod
    def _enforce_review_contract(
        assessment: dict[str, Any],
        review_mode: str,
        repeated_questions: list[str] | None = None,
    ) -> dict[str, Any]:
        """代码层校验审批 JSON，防止模型口头 APPROVE 却同时返回失败检查项。"""
        normalized = dict(assessment)
        decision = str(normalized.get("decision") or "").upper()
        if decision == "RETRIEVE":
            # RETRIEVE 只能在调用检索之前短暂存在；走到发送判断时仍未解决就必须停下。
            normalized["decision"] = "HANDOFF"
            normalized["reason"] = str(normalized.get("reason") or "公共事实检索后仍未完成情景核验")
            return normalized
        if decision != "APPROVE":
            return normalized

        checks = normalized.get("checks") if isinstance(normalized.get("checks"), dict) else {}
        required_checks = (
            "contextCoherent",
            "personaAligned",
            "speakerConsistent",
            "worldKnowledgeConsistent",
            "naturalConversation",
            "answersLatestMessage",
            "currentStateGrounded",
        )
        failed_checks = [name for name in required_checks if checks.get(name) is not True]
        # 旧模型或兼容 API 可能不会返回新增检查字段，因此不能把它设为所有回复的硬性必填项。
        # 只有候选全部由已回答问题组成、代码层无法留下任何可发送内容时，才强制模型改写或接管。
        if repeated_questions and "doesNotRepeatAnsweredQuestion" not in failed_checks:
            failed_checks.append("doesNotRepeatAnsweredQuestion")
        unsupported_claims = normalized.get("unsupportedPersonalClaims") or []
        entity_conflicts = normalized.get("entityConflicts") or []
        if not failed_checks and not unsupported_claims and not entity_conflicts:
            return normalized

        normalized["failedChecks"] = failed_checks
        details: list[str] = []
        if failed_checks:
            details.append("未明确通过检查：" + "、".join(failed_checks))
        if unsupported_claims:
            details.append("存在无依据的用户事实")
        if entity_conflicts:
            details.append("存在实体或世界知识冲突")
        normalized["reason"] = "；".join(details)
        fallback = str(normalized.get("rewrittenDraft") or "").strip()
        if review_mode == "AUTO_REWRITE":
            normalized["decision"] = "REWRITE"
        else:
            normalized["decision"] = "HANDOFF"
        return normalized

    @classmethod
    def _remove_answered_repeated_questions(
        cls,
        candidate: str,
        history: list[dict[str, Any]],
        current_peer_text: str,
    ) -> tuple[str, list[str]]:
        """删除已被回答却再次出现的问题气泡，避免长流程 Skill 从头开始问。"""
        parts = [part.strip() for part in str(candidate or "").splitlines() if part.strip()]
        if not parts or not history:
            return str(candidate or "").strip(), []

        # 当前入站消息还未进入历史查询结果，必须临时追加为最后一条“对方”消息，
        # 否则模型刚问完、对方刚回答的最常见场景无法被识别。
        conversation = [*history]
        if str(current_peer_text or "").strip():
            conversation.append({"role": "peer", "text": str(current_peer_text).strip()})

        kept: list[str] = []
        removed: list[str] = []
        for part in parts:
            if cls._is_answered_repeated_question(part, conversation):
                removed.append(part)
            else:
                kept.append(part)
        return "\n".join(kept), removed

    @classmethod
    def _is_answered_repeated_question(cls, candidate_part: str, history: list[dict[str, Any]]) -> bool:
        """判断候选问题是否与历史中的己方问题相同，并确认其后已有对方答复。"""
        if not cls._looks_like_question(candidate_part):
            return False
        normalized_candidate = cls._normalize_question(candidate_part)
        if len(normalized_candidate) < 3:
            return False

        for index, item in enumerate(history):
            if not cls._is_self_history_message(item):
                continue
            historical_text = str(item.get("text") or "").strip()
            if not cls._looks_like_question(historical_text):
                continue
            normalized_history = cls._normalize_question(historical_text)
            similarity = SequenceMatcher(None, normalized_candidate, normalized_history).ratio()
            same_question = (
                normalized_candidate == normalized_history
                or similarity >= 0.84
                or (
                    min(len(normalized_candidate), len(normalized_history)) >= 6
                    and (
                        normalized_candidate in normalized_history
                        or normalized_history in normalized_candidate
                    )
                )
            )
            if not same_question:
                continue
            if any(
                not cls._is_self_history_message(later)
                and str(later.get("text") or "").strip()
                for later in history[index + 1:]
            ):
                return True
        return False

    @staticmethod
    def _looks_like_question(text: str) -> bool:
        """用通用疑问表达识别问题，不依赖任何具体 Skill 的字段名称。"""
        normalized = "".join(str(text or "").split())
        if not normalized:
            return False
        return bool(
            re.search(r"[?？]", normalized)
            or any(
                cue in normalized
                for cue in ("多少", "哪个", "哪里", "什么", "怎么", "如何", "是否", "能不能", "可不可以", "有没有", "吗", "呢")
            )
        )

    @staticmethod
    def _normalize_question(text: str) -> str:
        """去除空白和标点后比较问题语义骨架，保留中文、字母与数字。"""
        return re.sub(r"[^\w\u4e00-\u9fff]", "", str(text or "").lower(), flags=re.UNICODE)

    @staticmethod
    def _is_self_history_message(item: dict[str, Any]) -> bool:
        """统一识别己方真人消息和代理已发送消息，二者都可能形成已问过的问题。"""
        origin = str(item.get("messageOrigin") or "").upper()
        authority = str(item.get("factAuthority") or "")
        return (
            str(item.get("role") or "") == "self"
            or authority == "agent_output"
            or origin in {"AGENT_AUTO", "AGENT_CONFIRMED", "USER_MANUAL"}
        )

    @staticmethod
    def _format_public_knowledge(items: list[dict[str, Any]]) -> str:
        """把公共检索摘要标成不可信外部资料，仅供核对公开实体关系。"""
        fragments: list[str] = []
        for item in items[:4]:
            title = str(item.get("title") or "").strip()
            content = " ".join(str(item.get("content") or "").split()).strip()
            url = str(item.get("url") or "").strip()
            if content:
                fragments.append(f"[外部资料] {title}\n{content}\n来源：{url}")
        return "\n\n".join(fragments)

    @staticmethod
    def _parse_assessment(response: str) -> dict[str, Any]:
        """校验模型 JSON 决策，未知字段可以保留，但关键类型必须收敛。"""
        raw = str(response or "").strip()
        if raw.startswith("```json"):
            raw = raw[len("```json"):]
        elif raw.startswith("```"):
            raw = raw[3:]
        if raw.endswith("```"):
            raw = raw[:-3]
        data = json.loads(raw.strip())
        if not isinstance(data, dict):
            raise ValueError("情景审查模型没有返回 JSON 对象")
        decision = str(data.get("decision") or "").upper()
        if decision not in {"APPROVE", "REWRITE", "RETRIEVE", "HANDOFF"}:
            raise ValueError("情景审查模型返回了未知决策")
        checks = data.get("checks") if isinstance(data.get("checks"), dict) else {}
        queries = data.get("searchQueries") if isinstance(data.get("searchQueries"), list) else []
        claims = data.get("unsupportedPersonalClaims")
        conflicts = data.get("entityConflicts")
        return {
            **data,
            "decision": decision,
            "reason": str(data.get("reason") or "").strip(),
            "rewrittenDraft": str(data.get("rewrittenDraft") or "").strip(),
            "searchQueries": [str(query) for query in queries[:2]],
            "checks": checks,
            "unsupportedPersonalClaims": claims if isinstance(claims, list) else [],
            "entityConflicts": conflicts if isinstance(conflicts, list) else [],
        }

    def _approved_result(
        self,
        task_context: AgentTaskContext,
        reviewed_draft: str,
        decision: str,
        reason: str,
        checks: dict[str, Any],
        original_draft: str | None = None,
        assessment: dict[str, Any] | None = None,
        public_knowledge: list[dict[str, Any]] | None = None,
    ) -> AgentResult:
        """构造可继续进入最终 ReviewAgent 的稳定审查结果。"""
        assessment = assessment or {}
        public_knowledge = public_knowledge or []
        original = original_draft if original_draft is not None else reviewed_draft
        rewrite_pending = decision == "REWRITE" and reviewed_draft == original
        return AgentResult(
            task_id=task_context.task_id,
            agent=self.name,
            status=("rewrite_pending" if rewrite_pending else "rewritten" if reviewed_draft != original else "approved"),
            structured_result={
                "contextDecision": decision,
                "reviewedDraft": reviewed_draft,
                "originalDraft": original,
                "contextRewritten": reviewed_draft != original,
                "contextReviewReason": reason,
                # 为 true 时表示情景层没有可直接采用的改写，最终审批层必须继续执行纠偏循环。
                "contextRewritePending": rewrite_pending,
                "checks": checks,
                "unsupportedPersonalClaims": assessment.get("unsupportedPersonalClaims") or [],
                "entityConflicts": assessment.get("entityConflicts") or [],
                "publicSearchUsed": bool(public_knowledge),
                "publicSearchQueries": [item.get("query") for item in public_knowledge if item.get("query")],
                "publicKnowledge": public_knowledge,
            },
        )

    def _handoff(
        self,
        task_context: AgentTaskContext,
        candidate: str,
        reason: str,
        assessment: dict[str, Any] | None = None,
        public_knowledge: list[dict[str, Any]] | None = None,
    ) -> AgentResult:
        """构造情景无法安全闭合时的人工接管事项，并保留原草稿供用户参考。"""
        assessment = assessment or {}
        public_knowledge = public_knowledge or []
        checks = assessment.get("checks") or {}
        review_unavailable = bool(checks.get("reviewUnavailable"))
        history = self._format_history(task_context.history_context[-6:])
        incoming = (task_context.event.text or "").strip()
        progress = "\n".join(part for part in (history, f"对方：{incoming}" if incoming else "") if part)
        return AgentResult(
            task_id=task_context.task_id,
            agent=self.name,
            status="needs_human",
            structured_result={
                "contextDecision": "HANDOFF",
                "handoffRequired": True,
                "handoffReason": reason,
                "handoffSummary": (
                    "情景审查服务暂时不可用，未经审查的回复没有发送。"
                    if review_unavailable
                    else "情景一致性审查未通过，自动回复已停止。"
                ),
                "conversationProgress": f"近期聊天：{progress}" if progress else "请查看当前会话后接管。",
                "proposedDraft": candidate,
                "checks": checks,
                "unsupportedPersonalClaims": assessment.get("unsupportedPersonalClaims") or [],
                "entityConflicts": assessment.get("entityConflicts") or [],
                "publicSearchUsed": bool(public_knowledge),
                "publicKnowledge": public_knowledge,
            },
            next_actions=["notify_user_handoff", "wait_for_human_approval"],
            need_confirmation=True,
        )
