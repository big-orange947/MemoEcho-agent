from __future__ import annotations

import json

from app.agents.base import BaseAgent
from app.clients.llm_service import LlmServiceClient
from app.schemas.results import AgentResult
from app.schemas.tasks import AgentTaskContext
from app.services.qq_reply_formatter import QqReplyFormatter
from app.services.conversation_prompt_compiler import ConversationPromptCompiler
from app.services.delegated_task_evidence import DelegatedTaskEvidenceBuilder
from app.services.skill_review_evidence import SkillReviewEvidenceBuilder


class ReviewAgent(BaseAgent):
    """自动发送前的独立审批 Agent；严格模式转人工，自动纠偏模式改写后放行。"""

    name = "review"
    MAX_AUTO_REWRITE_ATTEMPTS = 3

    def __init__(self, tools, llm_client: LlmServiceClient | None = None) -> None:
        super().__init__(tools)
        self.llm_client = llm_client

    async def run(self, task_context: AgentTaskContext, action: str) -> AgentResult:
        """读取 SocialAgent 候选回复和证据，只有明确 APPROVE 才允许后续回写。"""
        previous = task_context.metadata.get("previous_results") or {}
        social = previous.get("social") or {}
        context_review = previous.get("context_review") or {}
        candidate = str(
            context_review.get("reviewedDraft")
            or social.get("draft")
            or social.get("proposedDraft")
            or ""
        ).strip()
        profile = self._extract_profile(task_context)
        # 审查层必须看到 Profile 2.0 的结构化事实，而不能只读取旧版自由提示词。
        authorized_prompt = ConversationPromptCompiler().compile(profile, include_legacy_prompt=True)
        authorized_prompt = self._append_conversation_state_evidence(
            authorized_prompt,
            task_context.conversation_state,
        )
        authorized_prompt = self._append_verified_memory_evidence(
            authorized_prompt,
            task_context.verified_memories,
        )
        delegated_task = (task_context.metadata or {}).get("delegated_task")
        delegated_action = (task_context.metadata or {}).get("delegated_task_action")
        authorized_prompt = self._append_delegated_task_evidence(
            authorized_prompt,
            delegated_task,
            delegated_action,
        )
        asset_requests = [str(item).strip() for item in (social.get("assetRequests") or []) if str(item).strip()]
        asset_evidence, invalid_asset_ids = self._build_asset_request_evidence(profile, asset_requests)
        if invalid_asset_ids:
            return self._reject(
                task_context,
                candidate,
                "候选回复请求了当前会话未授权的安全资产",
            )
        resolved_skills = self._extract_resolved_skills(task_context)
        # 最终审批只需要核对约束和事实，不应再次注入生成阶段使用的完整长 Skill。
        skill_evidence = SkillReviewEvidenceBuilder.build(resolved_skills)
        if asset_evidence:
            skill_evidence = "\n\n".join(part for part in (skill_evidence, asset_evidence) if part)
        knowledge_evidence = self._format_retrieved_knowledge(task_context.retrieved_knowledge)
        public_knowledge_evidence = self._format_retrieved_knowledge(
            context_review.get("publicKnowledge") or []
        )
        knowledge_evidence = "\n\n".join(
            part for part in (knowledge_evidence, public_knowledge_evidence) if part
        )
        if knowledge_evidence:
            skill_evidence = "\n\n".join(part for part in (skill_evidence, knowledge_evidence) if part)
        media_evidence = self._format_current_media_analysis(social.get("mediaAnalysis") or [])
        if media_evidence:
            skill_evidence = "\n\n".join(part for part in (skill_evidence, media_evidence) if part)
        # 委托启动事件属于控制面，没有“对方刚发来的消息”；审查时不能把它标成对方发言。
        incoming = "" if task_context.event.event_type == "delegated_task_started" else (
            task_context.event.text or ""
        ).strip()
        # 工作台委托本身要求 Agent 持续推进，因此普通事实不足应先安全改写，而不是继承设定集的严格接管默认值。
        review_mode = DelegatedTaskEvidenceBuilder.resolve_review_mode(profile, delegated_task)

        if context_review.get("handoffRequired") and review_mode != "AUTO_REWRITE":
            return self._reject(
                task_context,
                candidate,
                str(context_review.get("handoffReason") or "情景一致性审查要求人工接管"),
            )
        if social.get("handoffRequired") and review_mode != "AUTO_REWRITE":
            return self._reject(task_context, candidate, str(social.get("handoffReason") or "上游安全闸门要求接管"))
        if not candidate:
            return self._reject(task_context, candidate, "没有可审批的候选回复")

        model_profile = self._extract_model_profile(task_context)
        if self.llm_client is None or not self.llm_client.is_enabled(model_profile):
            return self._reject(task_context, candidate, "审批模型不可用，闭世界策略禁止默认放行")
        try:
            context_requires_rewrite = bool(
                context_review.get("handoffRequired")
                or context_review.get("contextRewritePending")
            )
            if review_mode == "AUTO_REWRITE" and context_requires_rewrite:
                # 情景层已经明确指出原草稿有问题时，不能因为最终审查偶然返回 APPROVE 而原样发送。
                return await self._auto_rewrite_until_send(
                    task_context=task_context,
                    social=social,
                    incoming=incoming,
                    candidate=candidate,
                    authorized_prompt=authorized_prompt,
                    skill_evidence=skill_evidence,
                    model_profile=model_profile,
                    initial_reason=str(
                        context_review.get("handoffReason")
                        or context_review.get("contextReviewReason")
                        or "情景审查要求纠偏"
                    ),
                    initial_rewritten_draft="",
                )
            decision, reason, rewritten_draft = await self._review_with_model(
                incoming, task_context.history_context, candidate, authorized_prompt,
                skill_evidence, model_profile
            )
            if decision != "APPROVE" and review_mode == "AUTO_REWRITE":
                return await self._auto_rewrite_until_send(
                    task_context=task_context,
                    social=social,
                    incoming=incoming,
                    candidate=candidate,
                    authorized_prompt=authorized_prompt,
                    skill_evidence=skill_evidence,
                    model_profile=model_profile,
                    initial_reason=reason,
                    initial_rewritten_draft=rewritten_draft,
                )
            if decision != "APPROVE":
                return self._reject(task_context, candidate, reason or "候选回复超出已授权信息范围")
        except Exception as exception:
            reason = (
                "最终审批服务暂时不可用：重试后仍发生网络超时，未发送未经审批的回复"
                if LlmServiceClient.is_transient_error(exception)
                else f"审批服务异常：{type(exception).__name__}"
            )
            return self._reject(task_context, candidate, reason)

        return self._approve(task_context, social, candidate, "候选回复有完整依据")

    async def _auto_rewrite_until_send(
        self,
        task_context: AgentTaskContext,
        social: dict,
        incoming: str,
        candidate: str,
        authorized_prompt: str,
        skill_evidence: str,
        model_profile: dict | None,
        initial_reason: str,
        initial_rewritten_draft: str,
    ) -> AgentResult:
        """在自动纠偏模式下最多改写并复审三次，达到上限后发送最后一次改写稿。"""
        reason = initial_reason or "候选回复未通过审批"
        suggested_rewrite = initial_rewritten_draft.strip()

        for attempt in range(1, self.MAX_AUTO_REWRITE_ATTEMPTS + 1):
            # 审查模型已给出改写时直接采用，否则调用专用纠偏提示词生成新的候选。
            rewritten = suggested_rewrite or await self._rewrite_with_model(
                incoming,
                task_context.history_context,
                candidate,
                authorized_prompt,
                skill_evidence,
                reason,
                model_profile,
            )
            if rewritten:
                candidate = rewritten

            decision, review_reason, suggested_rewrite = await self._review_with_model(
                incoming,
                task_context.history_context,
                candidate,
                authorized_prompt,
                skill_evidence,
                model_profile,
            )
            if decision == "APPROVE":
                return self._approve(
                    task_context,
                    social,
                    candidate,
                    review_reason or reason or "审查后自动纠偏",
                    rewrite_attempts=attempt,
                )

            reason = review_reason or reason
            if attempt == self.MAX_AUTO_REWRITE_ATTEMPTS:
                # 用户已选择“无需接管”。第三次纠偏仍被模型拒绝时，不再创建接管事项，
                # 而是发送最后一次改写稿，同时把强制放行原因写入执行元数据供事后汇报。
                return self._approve(
                    task_context,
                    social,
                    candidate,
                    reason or "自动纠偏达到重试上限",
                    rewrite_attempts=attempt,
                    forced_after_retries=True,
                )

        # 循环边界由常量控制，正常情况下不会走到这里。
        return self._approve(
            task_context,
            social,
            candidate,
            reason,
            rewrite_attempts=self.MAX_AUTO_REWRITE_ATTEMPTS,
            forced_after_retries=True,
        )

    @staticmethod
    def _append_conversation_state_evidence(authorized_prompt: str, conversation_state) -> str:
        """把确定性的待处理消息交给审查层，防止改写时颠倒双方或遗漏连续消息。"""
        if conversation_state is None or conversation_state.status == "IDLE":
            return authorized_prompt
        pending_lines = [
            f"- [{item.source_event_id or '无事件ID'}] {item.text}"
            for item in conversation_state.pending_items
        ]
        return (
            f"{authorized_prompt}\n\n"
            "[可信会话开放状态]\n"
            f"状态：{conversation_state.status}\n"
            f"当前责任方：{conversation_state.responsible_party}\n"
            f"说明：{conversation_state.summary}\n"
            f"待处理原文：\n{'\n'.join(pending_lines) if pending_lines else '- 无'}\n"
            "审查和改写只能围绕上述原文，不得颠倒双方身份，也不得据此补造业务事实。"
        )

    @staticmethod
    def _append_verified_memory_evidence(authorized_prompt: str, verified_memories) -> str:
        """把与生成层相同的已确认记忆交给审查层，避免合法事实因证据缺失被误拦截。"""
        if not verified_memories:
            return authorized_prompt
        lines = [
            f"- [memory:{memory.id}] {memory.subject} / {memory.predicate} / {memory.value}"
            for memory in verified_memories[:30]
        ]
        memory_text = "\n".join(lines)
        return (
            f"{authorized_prompt}\n\n"
            "[用户已确认的长期记忆证据]\n"
            f"{memory_text}\n"
            "这些事实可以引用，但不能推导或补造记录中没有的现实状态。"
        )

    @staticmethod
    def _append_delegated_task_evidence(
        authorized_prompt: str,
        delegated_task,
        delegated_action=None,
    ) -> str:
        """把委托契约作为独立授权证据交给审批层，避免合法的主动联系被误判为越权。"""
        return DelegatedTaskEvidenceBuilder.append(
            authorized_prompt,
            delegated_task,
            delegated_action,
        )

    def _approve(
        self,
        task_context: AgentTaskContext,
        social: dict,
        candidate: str,
        reason: str,
        rewrite_attempts: int = 0,
        forced_after_retries: bool = False,
    ) -> AgentResult:
        """统一清洗审批通过文本，确保自动纠偏和原始草稿都遵守同一套 QQ 发送样式。"""
        original = str(social.get("draft") or social.get("proposedDraft") or "").strip()
        profile = self._extract_profile(task_context)
        formatter = QqReplyFormatter()
        if social.get("reactionOnly"):
            # 审查改写不能把纯表情轻回应重新扩写成图片解说或主动追问。
            message_parts = formatter.format_reaction(
                candidate,
                task_context.event.event_id,
                media_evidence=self._format_current_media_analysis(social.get("mediaAnalysis") or []),
            )
        else:
            message_parts = formatter.format(
                candidate,
                task_context.event.event_id,
                max_reply_chars=self._resolve_max_reply_chars(profile),
                split_long_reply=bool(profile.get("splitLongReply", True)),
                split_reply_chance_percent=self._resolve_split_reply_chance(profile),
                main_console_mode=isinstance(
                    (task_context.metadata or {}).get("delegated_task"),
                    dict,
                ),
            )
        approved_draft = "\n".join(message_parts)
        return AgentResult(
            task_id=task_context.task_id,
            agent=self.name,
            status="approved",
            structured_result={
                "reviewDecision": "APPROVE",
                "approvedDraft": approved_draft,
                "messageParts": message_parts,
                "rewritten": candidate != original,
                "reviewReason": reason,
                "autoRewriteApplied": candidate != original,
                "autoRewriteAttempts": rewrite_attempts,
                "autoRewriteForcedAfterRetries": forced_after_retries,
                # 审查通过后只传递资产引用；真正解密仍由 Orchestrator 的专用工具完成。
                "assetRequests": list(social.get("assetRequests") or []),
            },
        )

    @staticmethod
    def _build_asset_request_evidence(profile: dict, asset_requests: list[str]) -> tuple[str, list[str]]:
        """把待执行资产的名称和使用条件交给审查模型，同时识别越权资产 ID。"""
        context = profile.get("profileContext") or profile.get("profile_context") or {}
        assets = context.get("assets") if isinstance(context, dict) else []
        references: dict[str, dict] = {}
        for item in assets or []:
            if not isinstance(item, dict):
                continue
            asset_id = str(item.get("assetId") or item.get("asset_id") or "").strip()
            if asset_id:
                references[asset_id] = item

        invalid = [asset_id for asset_id in asset_requests if asset_id not in references]
        lines: list[str] = []
        for asset_id in asset_requests:
            item = references.get(asset_id)
            if item is None:
                continue
            name = str(item.get("name") or asset_id).strip()
            condition = str(item.get("usageCondition") or item.get("usage_condition") or "").strip()
            lines.append(f"资产 {name}（{asset_id}）；使用条件：{condition or '未明确，不允许自动执行'}")
        evidence = "[待执行安全资产]\n" + "\n".join(lines) if lines else ""
        return evidence, invalid

    @staticmethod
    def _resolve_max_reply_chars(profile: dict) -> int:
        """读取设定集的单气泡长度，并限制在适合 QQ 私聊的安全范围内。"""
        try:
            return min(max(int(profile.get("maxReplyChars", 16)), 8), 18)
        except (TypeError, ValueError):
            return 16

    @staticmethod
    def _resolve_split_reply_chance(profile: dict) -> int:
        """读取设定集的分段概率，异常配置回退为百分之三十三。"""
        try:
            return min(max(int(profile.get("splitReplyChancePercent", 33)), 0), 100)
        except (TypeError, ValueError):
            return 33

    async def _rewrite_with_model(
        self, incoming, history, candidate, authorized_prompt, skill_evidence, reason, model_profile
    ) -> str:
        """在无需接管模式下删除无依据内容，生成仍能自然推进对话的短回复。"""
        history_text = self._format_history_for_review(history)
        response = await self.llm_client.generate_reply(
            system_prompt=(
                "你是自动回复纠偏 Agent。只允许依据当前消息、历史聊天、用户提示词、Skill 和外部知识片段改写。"
                "删除所有无法证实的事实、账号、承诺和现实状态，不得补充新信息。"
                "回复要像 QQ 私聊，简短直接，优先使用一句话。若无法直接回答事实，"
                "只询问继续聊天真正缺少的一项信息；不要返回空内容，也不得使用“我先确认一下、我帮你问问”之类的中间人话术。"
                "历史中“我”是当前账号主人，“对方”是聊天联系人；候选回复只能代表“我”，绝不能混淆双方身份。"
                "标记为‘代理曾发送’的历史只能用于保持措辞连贯，不能作为用户经历、现实状态或当前活动的事实依据。"
                "历史只用于理解语境，改写必须回复当前最新消息，不得把旧话题改写成账号主人此刻正在进行的活动。"
                "不得输出括号动作、神态、心理活动或舞台说明，也不得凭空引入游戏角色、商品状态或其他新话题。"
                "只输出 JSON：{\"rewrittenDraft\":\"改写后的回复\"}。"
            ),
            user_message=(
                f"[用户提示词]\n{authorized_prompt or '无'}\n[Skill 与知识]\n{skill_evidence or '无'}\n"
                f"[历史聊天]\n{history_text or '无'}\n[当前消息]\n{incoming}\n"
                f"[原候选]\n{candidate}\n[审查原因]\n{reason or '缺少来源依据'}"
            ),
            temperature=0.1,
            model_profile=model_profile,
        )
        data = json.loads(response.strip().removeprefix("```json").removesuffix("```").strip())
        rewritten = str(data.get("rewrittenDraft", "")).strip()
        return rewritten

    @staticmethod
    def _format_retrieved_knowledge(knowledge_items: list[dict]) -> str:
        """将检索片段作为审查可引用来源传给模型，避免审查层忽略生成层已使用的资料。"""
        fragments: list[str] = []
        for item in knowledge_items[:3]:
            source = str(item.get("source", "")).strip()
            content = " ".join(str(item.get("content", "")).split()).strip()
            if source and content:
                fragments.append(f"[外部知识库: {source}]\n{content}")
        return "\n\n".join(fragments)

    @staticmethod
    def _format_current_media_analysis(analyses: list[dict]) -> str:
        """把当前附件的已验证解析结果纳入审查证据，防止审查层误判为模型凭空编造。"""
        fragments: list[str] = []
        for analysis in analyses:
            if not isinstance(analysis, dict):
                continue
            status = str(analysis.get("status", ""))
            extracted = " ".join(str(analysis.get("extractedText", "")).split())
            summary = " ".join(str(analysis.get("summary", "")).split())
            if status in {"VISION_ANALYZED", "TEXT_EXTRACTED"} and (extracted or summary):
                fragments.append(f"[当前附件] {extracted or summary}")
        return "\n".join(fragments[:4])

    async def _review_with_model(
        self, incoming, history, candidate, authorized_prompt, skill_evidence, model_profile
    ) -> tuple[str, str, str]:
        history_text = self._format_history_for_review(history)
        response = await self.llm_client.generate_reply(
            system_prompt=(
                "若存在[待执行安全资产]，只有当前消息和历史记录明确满足对应使用条件时才可 APPROVE；"
                "使用条件为空、仍需确认付款或交付状态、或资产与当前话题无关时必须 HANDOFF。"
                "\u5ba1\u67e5\u53ea\u4f9d\u636e\u5df2\u63d0\u4f9b\u7684\u5bf9\u8bdd\u5386\u53f2\u3001\u5f53\u524d\u6d88\u606f\u3001\u4f1a\u8bdd\u63d0\u793a\u8bcd\u548c Skill\u3002"
                "\u82e5\u8981\u7ee7\u7eed\u63a8\u8fdb\u5f53\u524d\u5bf9\u8bdd\uff0c\u5019\u9009\u56de\u590d\u4ecd\u7f3a\u5c11\u5fc5\u8981\u4fe1\u606f\u3001\u771f\u5b9e\u72b6\u6001\u3001\u7528\u6237\u6388\u6743\u6216\u7528\u6237\u51b3\u5b9a\uff0c\u5fc5\u987b HANDOFF\u3002"
                "用户会话提示词、Skill 和知识库中明确写出的事实属于用户授权依据，可以直接使用；"
                "只能使用明确值，不得自行补全账号、状态、金额、联系方式或现实结果。"
                "\u53ea\u6709\u5728\u4e0d\u9700\u8981\u4eba\u5de5\u5ba1\u6279\u65f6\u624d\u80fd\u8fd4\u56de REWRITE\uff0c\u5e76\u5728 rewrittenDraft \u4e2d\u7ed9\u51fa\u4e0d\u65b0\u589e\u4efb\u4f55\u4e8b\u5b9e\u7684\u7b80\u77ed\u6539\u5199\u3002"
                "历史聊天中“我”表示当前账号主人，“对方”表示聊天联系人。候选回复只能代表“我”，"
                "不得将任一方说过的话归到另一方，也不能把对方的陈述当作账号主人的事实。"
                "标记为‘代理曾发送’的内容不是用户亲口确认的信息，不能证明用户经历、现实状态或当前活动。"
                "你是自动回复发送前的独立审批 Agent，不生成、不润色也不补充回复。"
                "采用严格闭世界原则：可用信息来源仅限当前消息、提供的历史聊天、用户会话提示词、已加载 Skill。"
                "候选回复中的每个事实、身份、账号、联系方式、金额、时间、地点、操作步骤、承诺和结论，"
                "都必须能从上述来源直接找到依据；不能依靠常识猜测、模型记忆或自行推断。"
                "用户提示词或 Skill 明确授权的支付方式确认、预约意向等有限操作可以 APPROVE，"
                "但授权不允许你编造支付状态、账号、金额、联系方式或现实结果；这些仍一律 HANDOFF。"
                "不新增事实、只自然询问一项缺失信息的简短问句，应当 APPROVE，不能因为它没有给出结论而 HANDOFF。"
                "QQ 私聊候选不得把自己说成客服、助手或中间人，也不得使用‘我帮你问问、我先确认一下’。"
                "候选回复中的括号动作、舞台说明、无依据的当前活动或突然出现的新话题均不受证据支持，不能直接 APPROVE。"
                "当前消息若只是表情包、贴纸或动画表情，一个不包含事实和追问的简短情绪回应可以 APPROVE；"
                "不得要求它补充图片出处，也不得改写成主动开启新话题的问题。"
                "只有候选回复完全受证据支持且未越权时才 APPROVE。"
                "AUTO_REWRITE 场景中，如果删除无依据内容后可以安全继续，优先返回 REWRITE 并给出 rewrittenDraft；"
                "只有完全受证据支持时才返回 APPROVE。"
                "只输出 JSON：{\"decision\":\"APPROVE|REWRITE|HANDOFF\",\"reason\":\"具体原因\","
                "\"rewrittenDraft\":\"仅 REWRITE 时填写的短回复\"}。"
            ),
            user_message=(
                f"[用户会话提示词]\n{authorized_prompt or '无'}\n"
                f"[已加载 Skill]\n{skill_evidence or '无'}\n"
                f"[历史聊天]\n{history_text or '无'}\n"
                f"[当前消息]\n{incoming}\n[待审批候选回复]\n{candidate}"
            ),
            temperature=0.0,
            model_profile=model_profile,
        )
        data = json.loads(response.strip().removeprefix("```json").removesuffix("```").strip())
        decision = str(data.get("decision", "")).upper()
        if decision not in {"APPROVE", "HANDOFF", "REWRITE"}:
            raise ValueError("审批模型返回了未知决策")
        return decision, str(data.get("reason", "")), str(data.get("rewrittenDraft", "")).strip()

    @staticmethod
    def _format_history_for_review(history: list[dict]) -> str:
        """标注双方、时间和代理来源；代理旧草稿只能衔接语境，不能证明用户事实。"""
        lines: list[str] = []
        for item in history[-12:]:
            text = " ".join(str(item.get("text", "")).split()).strip()
            if not text:
                continue
            origin = str(item.get("messageOrigin") or "EXTERNAL").upper()
            authority = str(item.get("factAuthority") or "")
            if authority == "derived_summary" or bool(item.get("derivedSummary")):
                speaker = "较早对话派生摘录（低权威，不可单独证明事实）"
            elif authority == "agent_output" or origin in {"AGENT_AUTO", "AGENT_CONFIRMED"}:
                speaker = "代理曾以我的账号发送（非用户事实）"
            else:
                speaker = "我" if str(item.get("role", "")) == "self" else "对方"
            timestamp = str(item.get("timestamp") or "").strip()
            time_label = f"[{timestamp}] " if timestamp else ""
            lines.append(f"{time_label}{speaker}：{text}")
        return "\n".join(lines)

    def _reject(self, task_context: AgentTaskContext, candidate: str, reason: str) -> AgentResult:
        # 内部启动事件没有对方发言，不能在接管摘要里伪造成“对方：工作台命令”。
        incoming = "" if task_context.event.event_type == "delegated_task_started" else (
            task_context.event.text or ""
        ).strip()
        history = task_context.history_context[-4:]
        progress = self._format_history_for_review(history)
        if incoming:
            progress = "\n".join(part for part in (progress, f"对方：{incoming}") if part)
        return AgentResult(
            task_id=task_context.task_id,
            agent=self.name,
            status="needs_human",
            structured_result={
                "reviewDecision": "HANDOFF",
                "handoffRequired": True,
                "handoffReason": reason,
                "handoffSummary": "审批未通过，自动回复已停止。",
                "conversationProgress": f"近期聊天：{progress}" if progress else "请查看近期聊天后接管。",
                "proposedDraft": candidate,
            },
            next_actions=["notify_user_handoff", "wait_for_human_approval"],
            need_confirmation=True,
        )
