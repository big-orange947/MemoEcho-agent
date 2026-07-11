from __future__ import annotations

from app.agents.base import BaseAgent
from app.agents.schedule_extractor import ScheduleCandidate, ScheduleExtractor
from app.clients.llm_service import LlmServiceClient
from app.schemas.model_profiles import ResolvedUserModelProfile
from app.schemas.results import AgentResult, ToolCallRecord
from app.schemas.tasks import AgentTaskContext


class ScheduleAgent(BaseAgent):
    name = "schedule"

    def __init__(self, tools, llm_client: LlmServiceClient | None = None) -> None:
        # 这个构造函数的作用是初始化日程提取器，并按需注入大模型客户端。
        super().__init__(tools)
        self.extractor = ScheduleExtractor()
        self.llm_client = llm_client

    async def run(self, task_context: AgentTaskContext, action: str) -> AgentResult:
        # 这个函数的作用是提取日程、尝试落库，并生成可直接回显的日程处理结果。
        text = task_context.event.text or ""
        candidate = self.extractor.extract(text)
        profile = self._extract_profile(task_context)
        resolved_skills = self._extract_resolved_skills(task_context)
        skill_references = self._extract_skill_references(profile)
        extracted = {
            "title": candidate.title,
            "start_time": candidate.start_time,
            "end_time": candidate.end_time,
            "location": candidate.location,
            "content": candidate.content,
            "participants": candidate.participants,
            "confidence": candidate.confidence,
            "source_chat_id": task_context.event.chat_id,
            "skillReferences": skill_references,
            "resolvedSkills": [skill.model_dump(by_alias=True) for skill in resolved_skills],
            "modelProfileId": profile.get("modelProfileId", ""),
            "allowedTools": profile.get("allowedTools", []),
        }
        tool_calls: list[ToolCallRecord] = []
        next_actions: list[str] = []

        if candidate.start_time:
            payload = {
                "sourceEventId": task_context.event.event_id,
                "platform": task_context.event.platform,
                "chatId": task_context.event.chat_id,
                "senderId": task_context.event.sender.id,
                "title": candidate.title,
                "startTime": candidate.start_time,
                "endTime": candidate.end_time,
                "location": candidate.location,
                "content": candidate.content,
                "participants": candidate.participants,
                "confidence": candidate.confidence,
            }
            tool_calls.append(ToolCallRecord(tool="create_schedule", arguments=payload))
            try:
                create_schedule_tool = self._get_tool(task_context, "create_schedule")
                persistence_result = await create_schedule_tool.execute(payload=payload)
                extracted["persisted_schedule"] = persistence_result
            except KeyError:
                next_actions.append("create_schedule tool is not registered")
            except PermissionError:
                next_actions.append("create_schedule tool is not allowed")
            except Exception as exc:
                extracted["persistence_error"] = str(exc)
                next_actions.append("retry_schedule_persistence")

        model_profile = self._extract_model_profile(task_context)
        llm_enabled = bool(self.llm_client and self.llm_client.is_enabled(model_profile))
        llm_reply = await self._generate_with_llm(candidate, extracted, model_profile, profile, resolved_skills)
        llm_used = llm_reply is not None
        reply = llm_reply or self._build_reply(candidate, extracted)

        extracted["llmEnabled"] = llm_enabled
        extracted["llmUsed"] = llm_used
        extracted["resolvedModelProfile"] = model_profile.model_dump(by_alias=True) if model_profile else None
        extracted["effectiveSystemPrompt"] = self._build_system_prompt(profile, resolved_skills)
        extracted["promptSource"] = self._build_prompt_source(profile, resolved_skills)

        return AgentResult(
            task_id=task_context.task_id,
            agent=self.name,
            status="success",
            structured_result=extracted,
            reply_draft=reply,
            tool_calls=tool_calls,
            next_actions=next_actions,
            need_confirmation=bool(profile.get("requireHumanConfirmation", False)),
        )

    async def _generate_with_llm(
        self,
        candidate: ScheduleCandidate,
        extracted: dict,
        model_profile: ResolvedUserModelProfile | None,
        profile: dict,
        resolved_skills,
    ) -> str | None:
        # 这个函数的作用是在有可用模型配置时生成更自然的日程确认回复，失败时自动回退。
        if self.llm_client is None or not self.llm_client.is_enabled(model_profile):
            return None

        system_prompt = self._build_system_prompt(profile, resolved_skills)
        user_message = (
            f"日程标题：{candidate.title}\n"
            f"开始时间：{candidate.start_time or '未识别'}\n"
            f"结束时间：{candidate.end_time or '未识别'}\n"
            f"地点：{candidate.location or '未识别'}\n"
            f"参与人：{candidate.participants or '未识别'}\n"
            f"是否已落库：{'是' if 'persisted_schedule' in extracted else '否'}\n"
            f"原始内容：{candidate.content}"
        )

        try:
            reply = await self.llm_client.generate_reply(
                system_prompt,
                user_message,
                temperature=0.3,
                model_profile=model_profile,
            )
            return reply.strip() if reply else None
        except Exception:
            return None

    def _build_system_prompt(self, profile: dict, resolved_skills) -> str:
        # 这个函数的作用是为日程整理场景拼接基础提示词和会话级补充设定。
        base_prompt = (
            "你是 Memo Echo 的日程整理助手。"
            "请根据结构化日程结果生成一条纯文本中文回复。"
            "不要使用 Markdown，不要编造不存在的信息，语气简洁明确。"
        )
        profile_instruction, _ = self._build_profile_instruction(
            profile,
            "以下是当前会话对日程整理助手的额外要求，请一并遵守：",
            resolved_skills=resolved_skills,
        )
        return f"{base_prompt}\n{profile_instruction}".strip() if profile_instruction else base_prompt

    def _build_prompt_source(self, profile: dict, resolved_skills) -> str:
        # 这个函数的作用是输出本次日程整理提示词来源，便于联调和前端展示。
        _, prompt_source = self._build_profile_instruction(
            profile,
            "以下是当前会话对日程整理助手的额外要求，请一并遵守：",
            resolved_skills=resolved_skills,
        )
        return prompt_source

    def _build_reply(self, candidate: ScheduleCandidate, extracted: dict) -> str:
        # 这个函数的作用是在不依赖大模型时，根据提取和落库结果生成稳定的纯文本回复。
        persisted = "persisted_schedule" in extracted
        if candidate.start_time and candidate.location and persisted:
            return f"已记录日程：{candidate.title}，开始时间 {candidate.start_time}，地点 {candidate.location}。"
        if candidate.start_time and persisted:
            return f"已记录日程：{candidate.title}，开始时间 {candidate.start_time}。"
        if candidate.start_time and candidate.location:
            return f"已提取候选日程：{candidate.title}，开始时间 {candidate.start_time}，地点 {candidate.location}。"
        if candidate.start_time:
            return f"已提取候选日程：{candidate.title}，开始时间 {candidate.start_time}。"
        return f"已识别到候选日程主题：{candidate.title}，但时间信息还不完整。"
