from __future__ import annotations

from datetime import datetime
from typing import Any

from app.agents.base import BaseAgent
from app.agents.task_extractor import TaskCandidate, TaskExtractor
from app.clients.llm_service import LlmServiceClient
from app.schemas.model_profiles import ResolvedUserModelProfile
from app.schemas.results import AgentResult, ToolCallRecord
from app.schemas.tasks import AgentTaskContext


class WorkAgent(BaseAgent):
    name = "work"

    def __init__(self, tools, llm_client: LlmServiceClient | None = None) -> None:
        # 这个构造函数的作用是初始化任务提取器，并按需注入大模型客户端。
        super().__init__(tools)
        self.extractor = TaskExtractor()
        self.llm_client = llm_client

    async def run(self, task_context: AgentTaskContext, action: str) -> AgentResult:
        # 这个函数的作用是统一处理工作场景请求，并在“创建任务”和“查询待办”两种模式间切换。
        if self._should_query_existing_tasks(task_context):
            return await self._run_task_query_mode(task_context)
        return await self._run_task_create_mode(task_context)

    async def _run_task_create_mode(self, task_context: AgentTaskContext) -> AgentResult:
        # 这个函数的作用是从消息和附件分析结果里提取任务，并尝试持久化到 task-service。
        source_text = self._build_source_text(task_context)
        candidate = self.extractor.extract(source_text)
        profile = self._extract_profile(task_context)
        resolved_skills = self._extract_resolved_skills(task_context)
        skill_references = self._extract_skill_references(profile)

        plan = {
            "mode": "task_create",
            "title": candidate.title,
            "description": candidate.description,
            "due_time": candidate.due_time,
            "priority": candidate.priority,
            "confidence": candidate.confidence,
            "actionable": candidate.actionable,
            "source_chat_id": task_context.event.chat_id,
            "used_file_analysis": self._has_file_analysis(task_context),
            "daily_plan": self._build_daily_plan(task_context, candidate),
            "skillReferences": skill_references,
            "resolvedSkills": [skill.model_dump(by_alias=True) for skill in resolved_skills],
            "modelProfileId": profile.get("modelProfileId", ""),
            "allowedTools": profile.get("allowedTools", []),
        }
        tool_calls: list[ToolCallRecord] = []
        next_actions: list[str] = []

        if candidate.actionable:
            payload = {
                "sourceEventId": task_context.event.event_id,
                "platform": task_context.event.platform,
                "chatId": task_context.event.chat_id,
                "senderId": task_context.event.sender.id,
                "title": candidate.title,
                "description": candidate.description,
                "dueTime": candidate.due_time,
                "priority": candidate.priority,
                "status": "pending",
                "confidence": candidate.confidence,
            }
            tool_calls.append(ToolCallRecord(tool="create_task", arguments=payload))
            try:
                create_task_tool = self._get_tool(task_context, "create_task")
                persistence_result = await create_task_tool.execute(payload=payload)
                plan["persisted_task"] = persistence_result
            except KeyError:
                next_actions.append("create_task tool is not registered")
            except PermissionError:
                next_actions.append("create_task tool is not allowed")
            except Exception as exc:
                plan["persistence_error"] = str(exc)
                next_actions.append("retry_task_persistence")

        model_profile = self._extract_model_profile(task_context)
        llm_enabled = bool(self.llm_client and self.llm_client.is_enabled(model_profile))
        llm_reply = await self._generate_create_reply_with_llm(task_context, candidate, plan, model_profile)
        llm_used = llm_reply is not None
        reply = llm_reply or self._build_create_reply(candidate, plan)

        plan["llmEnabled"] = llm_enabled
        plan["llmUsed"] = llm_used
        plan["resolvedModelProfile"] = model_profile.model_dump(by_alias=True) if model_profile else None
        plan["effectiveSystemPrompt"] = self._build_create_system_prompt(profile, resolved_skills)
        plan["promptSource"] = self._build_prompt_source(profile, resolved_skills)

        return AgentResult(
            task_id=task_context.task_id,
            agent=self.name,
            status="success",
            structured_result=plan,
            reply_draft=reply,
            tool_calls=tool_calls,
            next_actions=next_actions,
            need_confirmation=bool(profile.get("requireHumanConfirmation", False)),
        )

    async def _run_task_query_mode(self, task_context: AgentTaskContext) -> AgentResult:
        # 这个函数的作用是查询已有待办，并生成一份可执行的今日工作计划。
        profile = self._extract_profile(task_context)
        resolved_skills = self._extract_resolved_skills(task_context)
        query_params = self._build_task_query_params(task_context)
        tool_calls = [ToolCallRecord(tool="list_tasks", arguments={"params": query_params})]
        next_actions: list[str] = []
        tasks: list[dict[str, Any]] = []
        query_error: str | None = None

        try:
            list_tasks_tool = self._get_tool(task_context, "list_tasks")
            tasks = await list_tasks_tool.execute(params=query_params)
        except KeyError:
            next_actions.append("list_tasks tool is not registered")
            query_error = "list_tasks tool is not registered"
        except PermissionError:
            next_actions.append("list_tasks tool is not allowed")
            query_error = "list_tasks tool is not allowed"
        except Exception as exc:
            next_actions.append("retry_task_query")
            query_error = str(exc)

        result = self._build_task_query_result(task_context, tasks, query_params, query_error)
        model_profile = self._extract_model_profile(task_context)
        llm_enabled = bool(self.llm_client and self.llm_client.is_enabled(model_profile))
        llm_reply = await self._generate_query_reply_with_llm(task_context, result, model_profile)
        llm_used = llm_reply is not None
        reply = llm_reply or self._build_query_reply(result)

        result["llmEnabled"] = llm_enabled
        result["llmUsed"] = llm_used
        result["resolvedModelProfile"] = model_profile.model_dump(by_alias=True) if model_profile else None
        result["effectiveSystemPrompt"] = self._build_query_system_prompt(profile, resolved_skills)
        result["promptSource"] = self._build_prompt_source(profile, resolved_skills)
        result["skillReferences"] = self._extract_skill_references(profile)
        result["resolvedSkills"] = [skill.model_dump(by_alias=True) for skill in resolved_skills]
        result["modelProfileId"] = profile.get("modelProfileId", "")
        result["allowedTools"] = profile.get("allowedTools", [])

        return AgentResult(
            task_id=task_context.task_id,
            agent=self.name,
            status="success",
            structured_result=result,
            reply_draft=reply,
            tool_calls=tool_calls,
            next_actions=next_actions,
            need_confirmation=bool(profile.get("requireHumanConfirmation", False)),
        )

    def _should_query_existing_tasks(self, task_context: AgentTaskContext) -> bool:
        # 这个函数的作用是识别“我今天该做什么”这类查询语义，避免误走成新任务创建流程。
        text = (task_context.event.text or "").strip().lower()
        if not text:
            return False

        query_phrases = (
            "what should i do today",
            "what do i need to do today",
            "today tasks",
            "pending tasks",
            "show my tasks",
            "list tasks",
            "今天该做什么",
            "今天做什么",
            "今天有什么待办",
            "今天有哪些待办",
            "最近待办",
            "待办有哪些",
            "最近有什么任务",
            "我有哪些任务",
            "帮我看看待办",
            "工作安排",
        )
        if any(phrase in text for phrase in query_phrases):
            return True

        return any(word in text for word in ("什么", "哪些", "安排")) and any(
            hint in text for hint in ("待办", "任务", "工作", "今天")
        )

    def _build_source_text(self, task_context: AgentTaskContext) -> str:
        # 这个函数的作用是把原始消息文本和 FileAgent 的分析结果拼成统一的任务提取输入。
        text_parts: list[str] = []
        event_text = (task_context.event.text or "").strip()
        if event_text:
            text_parts.append(event_text)

        previous_results = task_context.metadata.get("previous_results", {})
        file_result = previous_results.get("file") or {}
        file_text = str(file_result.get("extracted_text") or "").strip()
        if file_text:
            text_parts.append(file_text)

        return "\n".join(part for part in text_parts if part).strip()

    def _has_file_analysis(self, task_context: AgentTaskContext) -> bool:
        # 这个函数的作用是标记本次任务提取是否复用了上游附件分析结果。
        previous_results = task_context.metadata.get("previous_results", {})
        return bool(previous_results.get("file"))

    def _build_daily_plan(self, task_context: AgentTaskContext, candidate: TaskCandidate) -> dict[str, Any]:
        # 这个函数的作用是基于单条任务提取结果生成今天就能执行的工作计划。
        due_time_label = candidate.due_time or "今天下班前"
        used_file_analysis = self._has_file_analysis(task_context)
        plan_mode = "attachment_driven" if used_file_analysis else "text_driven"

        focus = f"今天优先推进：{candidate.title}" if candidate.actionable else "今天先澄清具体任务目标"
        steps = self._build_execution_steps(candidate, used_file_analysis)
        suggested_slots = self._build_suggested_slots(candidate, used_file_analysis)

        return {
            "mode": plan_mode,
            "focus": focus,
            "deadline_hint": due_time_label,
            "steps": steps,
            "suggested_slots": suggested_slots,
        }

    def _build_execution_steps(self, candidate: TaskCandidate, used_file_analysis: bool) -> list[str]:
        # 这个函数的作用是把任务拆成几条今天可直接执行的步骤。
        steps = ["先确认最终交付物和完成标准，避免做偏。"]
        if used_file_analysis:
            steps.append("根据附件内容整理关键要求、截止时间和需要输出的材料。")
        steps.append(f"集中处理核心任务：{candidate.title}。")
        if candidate.due_time:
            steps.append("在截止前预留 30 分钟做自查、修订和提交。")
        else:
            steps.append("完成初稿后立即自查，并补齐遗漏信息。")
        return steps

    def _build_suggested_slots(self, candidate: TaskCandidate, used_file_analysis: bool) -> list[str]:
        # 这个函数的作用是给出建议时间切片，方便用户马上开工。
        slots = ["现在开始 15 分钟：明确任务目标和输出格式。"]
        if used_file_analysis:
            slots.append("接下来 30 分钟：阅读附件并提炼关键要求。")
        slots.append("随后 60 到 90 分钟：完成主体内容整理或产出。")
        if candidate.due_time:
            slots.append(f"截止前 30 分钟：围绕 {candidate.due_time} 做最终检查并提交。")
        else:
            slots.append("今天收尾前 20 分钟：复盘进度并确认是否还需要补材料。")
        return slots

    async def _generate_create_reply_with_llm(
        self,
        task_context: AgentTaskContext,
        candidate: TaskCandidate,
        plan: dict[str, Any],
        model_profile: ResolvedUserModelProfile | None,
    ) -> str | None:
        # 这个函数的作用是在任务创建场景下用大模型生成更自然的纯文本工作计划回复。
        if self.llm_client is None or not self.llm_client.is_enabled(model_profile):
            return None

        daily_plan = plan["daily_plan"]
        profile = self._extract_profile(task_context)
        resolved_skills = self._extract_resolved_skills(task_context)
        system_prompt = self._build_create_system_prompt(profile, resolved_skills)
        user_message = (
            f"原始消息：{task_context.event.text or ''}\n"
            f"任务标题：{candidate.title}\n"
            f"任务描述：{candidate.description}\n"
            f"截止时间：{candidate.due_time or '未识别'}\n"
            f"优先级：{candidate.priority}\n"
            f"是否可执行：{'是' if candidate.actionable else '否'}\n"
            f"是否已落库：{'是' if 'persisted_task' in plan else '否'}\n"
            f"今日重点：{daily_plan['focus']}\n"
            f"建议步骤：{'；'.join(daily_plan['steps'])}\n"
            f"建议时间安排：{'；'.join(daily_plan['suggested_slots'])}"
        )

        try:
            reply = await self.llm_client.generate_reply(
                system_prompt,
                user_message,
                temperature=0.4,
                model_profile=model_profile,
            )
            return reply.strip() if reply else None
        except Exception:
            return None

    async def _generate_query_reply_with_llm(
        self,
        task_context: AgentTaskContext,
        result: dict[str, Any],
        model_profile: ResolvedUserModelProfile | None,
    ) -> str | None:
        # 这个函数的作用是在待办查询场景下用大模型生成更自然的今日任务建议回复。
        if self.llm_client is None or not self.llm_client.is_enabled(model_profile):
            return None

        if result.get("query_error"):
            return None

        tasks_summary = []
        for task in result["tasks"][:5]:
            tasks_summary.append(
                f"{task['title']}|优先级:{task['priority']}|截止:{task['due_time'] or '未设置'}"
            )

        daily_plan = result["daily_plan"]
        profile = self._extract_profile(task_context)
        resolved_skills = self._extract_resolved_skills(task_context)
        system_prompt = self._build_query_system_prompt(profile, resolved_skills)
        user_message = (
            f"用户原始问题：{task_context.event.text or ''}\n"
            f"待办总数：{result['task_count']}\n"
            f"今天到期：{result['today_task_count']}\n"
            f"已逾期：{result['overdue_task_count']}\n"
            f"任务列表：{'；'.join(tasks_summary) if tasks_summary else '无'}\n"
            f"今日重点：{daily_plan['focus']}\n"
            f"建议步骤：{'；'.join(daily_plan['steps'])}\n"
            f"建议时间安排：{'；'.join(daily_plan['suggested_slots'])}"
        )

        try:
            reply = await self.llm_client.generate_reply(
                system_prompt,
                user_message,
                temperature=0.4,
                model_profile=model_profile,
            )
            return reply.strip() if reply else None
        except Exception:
            return None

    def _build_create_system_prompt(self, profile: dict[str, Any], resolved_skills) -> str:
        # 这个函数的作用是为任务创建场景拼接基础工作规划提示词和会话级补充设定。
        base_prompt = (
            "你是 Memo Echo 的工作规划助手。"
            "请根据输入内容生成一条纯文本中文回复。"
            "不要使用 Markdown，不要编造信息，语气清晰、可执行。"
        )
        profile_instruction, _ = self._build_profile_instruction(
            profile,
            "以下是当前会话对工作规划助手的额外要求，请一并遵守：",
            resolved_skills=resolved_skills,
        )
        return f"{base_prompt}\n{profile_instruction}".strip() if profile_instruction else base_prompt

    def _build_query_system_prompt(self, profile: dict[str, Any], resolved_skills) -> str:
        # 这个函数的作用是为待办查询场景拼接基础任务建议提示词和会话级补充设定。
        base_prompt = (
            "你是 Memo Echo 的工作安排助手。"
            "请根据当前待办列表给出一条纯文本中文建议。"
            "不要使用 Markdown，优先给出今天先做什么、为什么、怎么安排。"
        )
        profile_instruction, _ = self._build_profile_instruction(
            profile,
            "以下是当前会话对工作安排助手的额外要求，请一并遵守：",
            resolved_skills=resolved_skills,
        )
        return f"{base_prompt}\n{profile_instruction}".strip() if profile_instruction else base_prompt

    def _build_prompt_source(self, profile: dict[str, Any], resolved_skills) -> str:
        # 这个函数的作用是输出本次工作规划提示词来源，便于联调和前端展示。
        _, prompt_source = self._build_profile_instruction(
            profile,
            "以下是当前会话对工作规划助手的额外要求，请一并遵守：",
            resolved_skills=resolved_skills,
        )
        return prompt_source

    def _build_create_reply(self, candidate: TaskCandidate, plan: dict[str, Any]) -> str:
        # 这个函数的作用是在不依赖大模型时，把任务提取结果整理成纯文本工作计划回复。
        if not candidate.actionable:
            return "我暂时没有从这条消息里识别到明确的待办任务，建议你再补充一下目标、截止时间或交付物。"

        daily_plan = plan["daily_plan"]
        lines = [
            "我已经帮你整理出今天的工作计划：",
            f"任务主题：{candidate.title}",
        ]
        if candidate.due_time:
            lines.append(f"截止时间：{candidate.due_time}")
        lines.append(f"当前优先级：{candidate.priority}")
        lines.append(f"今日重点：{daily_plan['focus']}")
        lines.append("建议步骤：")
        for index, step in enumerate(daily_plan["steps"], start=1):
            lines.append(f"{index}. {step}")
        lines.append("建议时间安排：")
        for slot in daily_plan["suggested_slots"]:
            lines.append(f"- {slot}")

        if "persisted_task" in plan:
            lines.append("这项任务我也已经同步记录到待办里了。")

        return "\n".join(lines)

    def _build_task_query_params(self, task_context: AgentTaskContext) -> dict[str, Any]:
        # 这个函数的作用是根据聊天上下文构造待办查询参数。
        text = (task_context.event.text or "").lower()
        params: dict[str, Any] = {
            "chatId": task_context.event.chat_id,
            "onlyPending": True,
            "limit": 5,
        }
        if task_context.event.chat_type == "private":
            params["senderId"] = task_context.event.sender.id
        if any(keyword in text for keyword in ("today", "今天", "今日")):
            params["todayOnly"] = True
        if any(keyword in text for keyword in ("high priority", "高优先级", "紧急")):
            params["priority"] = "high"
        return params

    def _build_task_query_result(
        self,
        task_context: AgentTaskContext,
        tasks: list[dict[str, Any]],
        query_params: dict[str, Any],
        query_error: str | None,
    ) -> dict[str, Any]:
        # 这个函数的作用是把 task-service 返回的结果整理成结构化的今日工作计划。
        now = datetime.now()
        normalized_tasks = [self._normalize_task_item(item) for item in tasks]
        overdue_tasks = [item for item in normalized_tasks if item["due_datetime"] and item["due_datetime"] < now]
        today_tasks = [item for item in normalized_tasks if item["due_datetime"] and item["due_datetime"].date() == now.date()]
        upcoming_tasks = [
            item for item in normalized_tasks
            if item["due_datetime"] and item["due_datetime"].date() > now.date()
        ]
        no_due_tasks = [item for item in normalized_tasks if item["due_datetime"] is None]

        daily_plan = {
            "mode": "query_driven",
            "focus": self._build_query_focus(overdue_tasks, today_tasks, upcoming_tasks, no_due_tasks),
            "steps": self._build_query_steps(overdue_tasks, today_tasks, upcoming_tasks, no_due_tasks),
            "suggested_slots": self._build_query_slots(overdue_tasks, today_tasks, upcoming_tasks, no_due_tasks),
        }

        return {
            "mode": "task_query",
            "query_params": query_params,
            "query_error": query_error,
            "task_count": len(normalized_tasks),
            "today_task_count": len(today_tasks),
            "overdue_task_count": len(overdue_tasks),
            "tasks": [self._serialize_task_item(item) for item in normalized_tasks],
            "today_tasks": [self._serialize_task_item(item) for item in today_tasks],
            "overdue_tasks": [self._serialize_task_item(item) for item in overdue_tasks],
            "upcoming_tasks": [self._serialize_task_item(item) for item in upcoming_tasks],
            "no_due_tasks": [self._serialize_task_item(item) for item in no_due_tasks],
            "daily_plan": daily_plan,
            "source_chat_id": task_context.event.chat_id,
        }

    def _normalize_task_item(self, item: dict[str, Any]) -> dict[str, Any]:
        # 这个函数的作用是把 task-service 返回对象转成便于排序和格式化的内部结构。
        due_time = item.get("dueTime")
        due_datetime = self._parse_datetime(due_time)
        created_at = self._parse_datetime(item.get("createdAt"))
        return {
            "id": item.get("id"),
            "title": item.get("title") or "未命名任务",
            "description": item.get("description") or "",
            "priority": item.get("priority") or "normal",
            "status": item.get("status") or "pending",
            "due_time": due_time,
            "due_datetime": due_datetime,
            "created_at": created_at,
        }

    def _serialize_task_item(self, item: dict[str, Any]) -> dict[str, Any]:
        # 这个函数的作用是去掉内部 datetime 字段，保留适合输出的任务信息。
        return {
            "id": item["id"],
            "title": item["title"],
            "description": item["description"],
            "priority": item["priority"],
            "status": item["status"],
            "due_time": item["due_time"],
        }

    def _parse_datetime(self, value: Any) -> datetime | None:
        # 这个函数的作用是把任务时间字符串解析成 datetime，便于分组和排序。
        if not isinstance(value, str) or not value.strip():
            return None
        for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(value, pattern)
            except ValueError:
                continue
        return None

    def _build_query_focus(
        self,
        overdue_tasks: list[dict[str, Any]],
        today_tasks: list[dict[str, Any]],
        upcoming_tasks: list[dict[str, Any]],
        no_due_tasks: list[dict[str, Any]],
    ) -> str:
        # 这个函数的作用是为查询模式提炼当前最值得优先关注的执行焦点。
        if overdue_tasks:
            return f"先补上已逾期任务：{overdue_tasks[0]['title']}"
        if today_tasks:
            return f"今天优先完成：{today_tasks[0]['title']}"
        if upcoming_tasks:
            return f"提前推进最近任务：{upcoming_tasks[0]['title']}"
        if no_due_tasks:
            return f"先从无截止时间任务里推进：{no_due_tasks[0]['title']}"
        return "当前没有查到需要立刻推进的待办。"

    def _build_query_steps(
        self,
        overdue_tasks: list[dict[str, Any]],
        today_tasks: list[dict[str, Any]],
        upcoming_tasks: list[dict[str, Any]],
        no_due_tasks: list[dict[str, Any]],
    ) -> list[str]:
        # 这个函数的作用是把多条待办整理成今天可执行的步骤顺序。
        steps: list[str] = []
        if overdue_tasks:
            steps.append(f"先处理逾期任务：{overdue_tasks[0]['title']}。")
        if today_tasks:
            steps.append("把今天截止的任务排到前面，优先清空当日承诺。")
        if upcoming_tasks:
            steps.append("给最近几天的任务提前预留准备时间，避免再次堆积。")
        if no_due_tasks:
            steps.append("把没有明确截止时间的事项放到收尾时段推进。")
        if not steps:
            steps.append("当前没有查到待办，今天可以继续收集任务或主动规划新事项。")
        return steps

    def _build_query_slots(
        self,
        overdue_tasks: list[dict[str, Any]],
        today_tasks: list[dict[str, Any]],
        upcoming_tasks: list[dict[str, Any]],
        no_due_tasks: list[dict[str, Any]],
    ) -> list[str]:
        # 这个函数的作用是基于待办分布给出更贴近执行顺序的时间安排建议。
        slots: list[str] = []
        if overdue_tasks:
            slots.append("现在开始 30 分钟：先补逾期任务，避免继续滚雪球。")
        if today_tasks:
            slots.append("接下来 60 分钟：集中处理今天截止的事项。")
        if upcoming_tasks:
            slots.append("下午预留 30 分钟：提前准备最近一两天要交付的任务。")
        if no_due_tasks:
            slots.append("收尾阶段 20 分钟：推进无截止时间但需要持续积累的事项。")
        if not slots:
            slots.append("今天暂时没有查到待办，可以把时间留给新任务规划或资料整理。")
        return slots

    def _build_query_reply(self, result: dict[str, Any]) -> str:
        # 这个函数的作用是在不依赖大模型时，把已存待办查询结果整理成纯文本回复。
        if result.get("query_error"):
            return f"我刚刚查询待办时遇到了一点问题：{result['query_error']}"

        tasks = result["tasks"]
        if not tasks:
            if result["query_params"].get("todayOnly"):
                return "我暂时没有查到你今天需要推进的待办。"
            return "我暂时没有查到待办任务。"

        lines = [
            "我帮你看了当前待办，今天建议按这个顺序推进：",
            f"当前共查到 {result['task_count']} 条待办，其中今天 {result['today_task_count']} 条，逾期 {result['overdue_task_count']} 条。",
            f"今日重点：{result['daily_plan']['focus']}",
            "建议步骤：",
        ]
        for index, step in enumerate(result["daily_plan"]["steps"], start=1):
            lines.append(f"{index}. {step}")

        lines.append("优先待办：")
        for index, task in enumerate(tasks[:3], start=1):
            due_label = task["due_time"] or "无明确截止时间"
            lines.append(f"{index}. {task['title']}，优先级 {task['priority']}，截止 {due_label}")

        lines.append("建议时间安排：")
        for slot in result["daily_plan"]["suggested_slots"]:
            lines.append(f"- {slot}")
        return "\n".join(lines)
