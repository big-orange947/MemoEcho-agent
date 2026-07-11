from __future__ import annotations

from app.agents.base import BaseAgent
from app.clients.llm_service import LlmServiceClient
from app.schemas.results import AgentResult
from app.schemas.tasks import AgentTaskContext


class SocialAgent(BaseAgent):
    name = "social"

    def __init__(self, tools, llm_client: LlmServiceClient | None = None) -> None:
        # 这个构造函数的作用是初始化社交回复 agent，并按需注入大模型客户端。
        super().__init__(tools)
        self.llm_client = llm_client

    async def run(self, task_context: AgentTaskContext, action: str) -> AgentResult:
        # 这个函数的作用是基于当前消息、设定集和模型配置生成一条可直接发送的社交回复草稿。
        profile = self._extract_profile(task_context)
        resolved_skills = self._extract_resolved_skills(task_context)
        incoming_text = (task_context.event.text or "").strip()
        effective_system_prompt = self._build_effective_system_prompt(profile, resolved_skills)
        prompt_source = self._build_prompt_source(profile, resolved_skills)
        model_profile = self._extract_model_profile(task_context)
        skill_references = self._extract_skill_references(profile)

        llm_reply = await self._generate_with_llm(effective_system_prompt, incoming_text, model_profile)
        llm_enabled = bool(self.llm_client and self.llm_client.is_enabled(model_profile))
        llm_used = llm_reply is not None

        if llm_used:
            reply_draft = llm_reply
            style_tags = ["llm_generated"]
        else:
            style_tags = self._detect_style_tags(effective_system_prompt)
            reply_draft = self._build_rule_based_reply(incoming_text, style_tags)

        return AgentResult(
            task_id=task_context.task_id,
            agent=self.name,
            status="success",
            structured_result={
                "draft": reply_draft,
                "personaMode": profile.get("personaMode", "NONE"),
                "replyMode": profile.get("replyMode", "AUTO_REPLY"),
                "preferredRoute": profile.get("preferredRoute", ""),
                "skillReference": profile.get("skillReference", ""),
                "skillReferences": skill_references,
                "resolvedSkills": [skill.model_dump(by_alias=True) for skill in resolved_skills],
                "modelProfileId": profile.get("modelProfileId", ""),
                "allowedTools": profile.get("allowedTools", []),
                "styleTags": style_tags,
                "sourceText": incoming_text,
                "effectiveSystemPrompt": effective_system_prompt,
                "promptSource": prompt_source,
                "llmUsed": llm_used,
                "llmEnabled": llm_enabled,
                "resolvedModelProfile": model_profile.model_dump(by_alias=True) if model_profile else None,
            },
            reply_draft=reply_draft,
            need_confirmation=bool(profile.get("requireHumanConfirmation", False)),
        )

    async def _generate_with_llm(self, system_prompt: str, user_message: str, model_profile) -> str | None:
        # 这个函数的作用是在有可用模型配置时优先请求大模型生成回复，失败时回退到本地规则。
        if self.llm_client is None or not self.llm_client.is_enabled(model_profile):
            return None
        try:
            reply_text = await self.llm_client.generate_reply(
                system_prompt,
                user_message,
                model_profile=model_profile,
            )
            return reply_text.strip() if reply_text else None
        except Exception:
            return None

    def _build_effective_system_prompt(self, profile: dict, resolved_skills) -> str:
        # 这个函数的作用是构造最终给 agent 使用的系统提示词。
        base_prompt = (
            "你是 Memo Echo Agent 的社交回复助手。"
            "你的目标是根据当前消息生成一条可直接发送的中文回复草稿。"
            "优先保持自然、简洁，不要过度表演。"
        )
        profile_instruction, _ = self._build_profile_instruction(
            profile,
            "以下是当前会话的人格设定，请严格参考这段设定来组织语气和表达方式：",
            resolved_skills=resolved_skills,
        )
        return f"{base_prompt}\n{profile_instruction}".strip() if profile_instruction else base_prompt

    def _build_prompt_source(self, profile: dict, resolved_skills) -> str:
        # 这个函数的作用是标记最终提示词的来源，便于调试。
        _, prompt_source = self._build_profile_instruction(
            profile,
            "以下是当前会话的人格设定，请严格参考这段设定来组织语气和表达方式：",
            resolved_skills=resolved_skills,
        )
        return prompt_source

    def _detect_style_tags(self, effective_system_prompt: str) -> list[str]:
        # 这个函数的作用是从最终系统提示词里提取可执行的风格标签。
        normalized_prompt = effective_system_prompt.lower()
        style_tags: list[str] = []

        if any(keyword in normalized_prompt for keyword in ("简洁", "直接", "克制", "short", "concise")):
            style_tags.append("concise")
        if any(keyword in normalized_prompt for keyword in ("温柔", "亲切", "柔和", "gentle", "warm")):
            style_tags.append("warm")
        if any(keyword in normalized_prompt for keyword in ("专业", "可靠", "助理", "professional")):
            style_tags.append("professional")
        if any(keyword in normalized_prompt for keyword in ("冷静", "理性", "清醒", "calm")):
            style_tags.append("calm")

        if not style_tags:
            style_tags.append("neutral")
        return style_tags

    def _build_rule_based_reply(self, incoming_text: str, style_tags: list[str]) -> str:
        # 这个函数的作用是在没有可用大模型时，根据消息内容套用不同风格模板生成草稿。
        text = incoming_text.strip()
        if not text:
            return self._apply_style("收到。", style_tags)

        if any(keyword in text for keyword in ("在吗", "在不在", "在么")):
            return self._apply_style("我在，你说。", style_tags)

        if any(keyword in text for keyword in ("谢谢", "感谢", "辛苦")):
            return self._apply_style("不客气，有需要随时说。", style_tags)

        if any(keyword in text for keyword in ("紧急", "马上", "尽快", "急")):
            return self._apply_style("收到，我优先处理，稍后给你结果。", style_tags)

        if text.endswith("?") or text.endswith("？"):
            return self._apply_style(f"收到，你这个问题我来跟进：{self._shorten(text)}", style_tags)

        return self._apply_style(f"收到，我先记下这件事：{self._shorten(text)}", style_tags)

    def _apply_style(self, base_reply: str, style_tags: list[str]) -> str:
        # 这个函数的作用是按风格标签对基础回复做轻量改写。
        if "warm" in style_tags and "professional" in style_tags:
            return f"{base_reply} 我会尽量帮你处理清楚。"
        if "warm" in style_tags:
            return f"{base_reply} 有需要的话我继续帮你。"
        if "professional" in style_tags or "calm" in style_tags:
            return f"{base_reply} 我会按优先级继续处理。"
        if "concise" in style_tags:
            return base_reply
        return f"{base_reply} 我继续跟进。"

    def _shorten(self, text: str, limit: int = 28) -> str:
        # 这个函数的作用是截断过长输入，避免回显过多原文。
        cleaned_text = " ".join(text.split())
        if len(cleaned_text) <= limit:
            return cleaned_text
        return cleaned_text[:limit] + "..."
