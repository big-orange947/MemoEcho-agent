from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from app.schemas.model_profiles import ResolvedUserModelProfile
from app.schemas.results import AgentResult
from app.schemas.skills import SkillDescriptor
from app.schemas.tasks import AgentTaskContext
from app.tools.base import ToolExecutionContext
from app.tools.registry import ToolRegistry


class BaseAgent(ABC):
    name: str

    def __init__(self, tools: ToolRegistry) -> None:
        # 这个构造函数的作用是为所有 Agent 挂载统一的工具注册表。
        self.tools = tools

    @abstractmethod
    async def run(self, task_context: AgentTaskContext, action: str) -> AgentResult:
        raise NotImplementedError

    def _get_tool(self, task_context: AgentTaskContext, name: str) -> Any:
        # 这个函数的作用是按当前会话允许的工具白名单取工具，未授权时直接阻断调用。
        allowed_tools = task_context.allowed_tools or []
        if allowed_tools and name not in allowed_tools:
            raise PermissionError(f"tool '{name}' is not allowed in current context")
        return self.tools.get(name)

    def _tool_execution_context(self, task_context: AgentTaskContext) -> ToolExecutionContext:
        # 这个函数的作用是把 AgentTaskContext 转换为工具注册表可校验的调用上下文。
        # 用户标识优先使用当前登录 QQ，避免把聊天对方误当成工具操作的所有者。
        event = task_context.event
        user_id = str(event.self_id or event.sender.id or "")
        return ToolExecutionContext(
            user_id=user_id,
            event_id=event.event_id,
            task_id=task_context.task_id,
            allowed_tools=frozenset(task_context.allowed_tools),
            trusted_internal=not bool(task_context.allowed_tools),
        )

    async def _invoke_tool(
        self,
        task_context: AgentTaskContext,
        name: str,
        arguments: dict[str, Any],
        *,
        idempotency_key: str = "",
    ) -> Any:
        # 这个函数的作用是让所有 Agent 统一经由 LangChain BaseTool.ainvoke 调用能力。
        # 注册表在这里负责授权、幂等和审计边界，Agent 不直接访问底层 HTTP 客户端或旧 execute 协议。
        return await self.tools.ainvoke(
            name,
            context=self._tool_execution_context(task_context),
            idempotency_key=idempotency_key,
            arguments=arguments,
        )

    def _extract_profile(self, task_context: AgentTaskContext) -> dict[str, Any]:
        # 这个函数的作用是从运行时上下文里提取命中的会话设定，供各类 Agent 复用。
        metadata = task_context.metadata or {}
        match_result = metadata.get("conversation_profile_match") or {}
        profile = match_result.get("profile") or {}
        return profile if isinstance(profile, dict) else {}

    def _extract_model_profile(self, task_context: AgentTaskContext) -> ResolvedUserModelProfile | None:
        # 这个函数的作用是从运行时上下文里提取已经解析完成的模型配置。
        metadata = task_context.metadata or {}
        resolved = metadata.get("resolved_model_profile") or {}
        profile = resolved.get("profile")
        if not isinstance(profile, dict):
            return None
        try:
            return ResolvedUserModelProfile.model_validate(profile)
        except Exception:
            return None

    def _extract_skill_references(self, profile: dict[str, Any]) -> list[str]:
        # 这个函数的作用是兼容旧版单 skill 字段和新版多 skill 列表字段。
        skill_references = profile.get("skillReferences") or []
        if not isinstance(skill_references, list):
            skill_references = []

        normalized: list[str] = []
        for item in skill_references:
            text = str(item).strip()
            if text and text not in normalized:
                normalized.append(text)

        single_reference = str(profile.get("skillReference", "") or "").strip()
        if single_reference and single_reference not in normalized:
            normalized.append(single_reference)
        return normalized

    def _extract_resolved_skills(self, task_context: AgentTaskContext) -> list[SkillDescriptor]:
        # 这个函数的作用是从运行时上下文里提取已经解析成功的 skill 描述符。
        metadata = task_context.metadata or {}
        payloads = metadata.get("resolved_skills") or []
        if not isinstance(payloads, list):
            return []

        resolved_skills: list[SkillDescriptor] = []
        for payload in payloads:
            if not isinstance(payload, dict):
                continue
            try:
                resolved_skills.append(SkillDescriptor.model_validate(payload))
            except Exception:
                continue
        return resolved_skills

    def _build_profile_instruction(
        self,
        profile: dict[str, Any],
        prompt_intro: str,
        resolved_skills: list[SkillDescriptor] | None = None,
    ) -> tuple[str, str]:
        # 这个函数的作用是根据 personaMode 把会话级 prompt 和 skill 设定拼成统一提示词片段，并返回来源标记。
        persona_mode = str(profile.get("personaMode", "") or "").strip().upper()
        persona_prompt = str(profile.get("systemPrompt", "") or "").strip()
        skill_references = self._extract_skill_references(profile)
        resolved_skills = resolved_skills or []
        resolved_skill_prompts = [
            skill.prompt_fragments.system.strip()
            for skill in resolved_skills
            if skill.prompt_fragments.system.strip()
        ]
        parts: list[str] = []

        if persona_mode == "NONE":
            return "", "default_prompt"

        if persona_mode == "PROMPT":
            if not persona_prompt:
                return "", "default_prompt"
            return f"{prompt_intro}\n{persona_prompt}".strip(), "profile_prompt_only"

        if persona_mode == "SKILL":
            if skill_references:
                parts.append(
                    "当前会话已绑定以下 skills："
                    + "、".join(skill_references)
                    + "。请把这些 skills 视为当前任务的主要行为约束。"
                )
            if resolved_skill_prompts:
                parts.append("这些 skills 提供的系统约束如下：\n" + "\n".join(resolved_skill_prompts))
            if persona_prompt:
                parts.append(
                    "以下人格设定作为 skills 的补充约束，请在不冲突时尽量保持一致：\n"
                    + persona_prompt
                )
            if persona_prompt and skill_references:
                return "\n".join(parts).strip(), "skill_plus_profile_prompt"
            if skill_references:
                return "\n".join(parts).strip(), "skill_only"
            if persona_prompt:
                return f"{prompt_intro}\n{persona_prompt}".strip(), "profile_prompt_only"
            return "", "default_prompt"

        if skill_references:
            parts.append(
                "当前会话已绑定以下 skills："
                + "、".join(skill_references)
                + "。请把这些 skills 视为当前任务的行为约束。"
            )
        if resolved_skill_prompts:
            parts.append("这些 skills 提供的系统约束如下：\n" + "\n".join(resolved_skill_prompts))

        if persona_prompt:
            parts.append(f"{prompt_intro}\n{persona_prompt}")

        if persona_prompt and skill_references:
            source = "skill_plus_profile_prompt"
        elif persona_prompt:
            source = "profile_prompt_only"
        elif skill_references:
            source = "skill_only"
        else:
            source = "default_prompt"

        return ("\n".join(parts).strip(), source)
