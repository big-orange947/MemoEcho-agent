from app.schemas.events import UnifiedEvent
from app.schemas.profiles import ConversationProfileMatchResult


class RouterService:
    DESKTOP_ALLOWED_ROUTES = {
        "social_reply",
        "chat_summary",
        "task_plan",
        "schedule_extract",
        "file_analysis",
        "message_dispatch",
        "group_ops",
    }
    TASK_KEYWORDS = (
        "todo", "task", "work", "plan", "submit", "finish", "pending",
        "待办", "任务", "完成", "提交", "整理", "汇总", "处理", "跟进", "工作计划", "今天工作",
    )
    TASK_QUERY_KEYWORDS = (
        "what should i do today", "today tasks", "pending tasks", "list tasks",
        "今天该做什么", "今天做什么", "今天有什么待办", "今天有哪些待办", "最近待办",
        "待办有哪些", "最近有什么任务", "我有哪些任务", "帮我看看待办", "工作安排",
    )
    SCHEDULE_KEYWORDS = (
        "today", "schedule", "meeting", "deadline",
        "日程", "会议", "提醒", "行程",
    )

    def route(self, event: UnifiedEvent, profile_match: ConversationProfileMatchResult | None = None) -> str:
        # 这个函数的作用是根据消息内容、会话类型和设定集结果，把事件路由到最合适的 Agent 工作流。
        text = (event.text or "").lower()
        requested_desktop_route = self._resolve_desktop_route(event)
        if requested_desktop_route:
            return requested_desktop_route

        is_direct_message = event.platform == "desktop" or event.chat_type == "private" or self._is_at_self(event)
        active_profile = profile_match.profile if profile_match and profile_match.active else None

        if active_profile and active_profile.preferred_route:
            return active_profile.preferred_route

        if not is_direct_message:
            return "message_dispatch"

        # 私聊图片属于会话内容：先由视觉解析补足上下文，再交给社交回复和审查链路。
        # 文档附件仍保持文件分析工作流，避免打断已有的任务提取和日程创建能力。
        if is_direct_message and any((attachment.file_type or "").lower() == "image" for attachment in event.attachments):
            return "social_reply"
        if event.attachments:
            return "file_analysis"
        if self._looks_like_task_route(text):
            return "task_plan"
        if any(keyword in text for keyword in self.SCHEDULE_KEYWORDS):
            return "schedule_extract"
        if event.chat_type == "private":
            return "social_reply"
        if self._looks_like_group_operation(text):
            return "group_ops"
        return "chat_summary"

    @staticmethod
    def _looks_like_group_operation(text: str) -> bool:
        """仅对明确群管理词汇触发 GroupOps，避免普通聊天误进入管理链路。"""
        keywords = (
            "/group", "group info", "group members", "group notices", "mute", "unmute",
            "群信息", "群资料", "群成员", "成员列表", "群公告", "群文件", "禁言列表",
            "禁言", "踢出", "踢人", "群名片", "设置群名", "管理员", "精华消息", "设为精华",
        )
        return any(keyword in text for keyword in keywords)

    def _resolve_desktop_route(self, event: UnifiedEvent) -> str | None:
        # 这个函数的作用是读取桌面客户端显式选择的工作流，并用白名单阻止任意内部路由注入。
        if event.platform != "desktop" or event.event_type != "desktop_command" or not event.raw_payload:
            return None
        requested_route = str(event.raw_payload.get("requestedRoute") or "").strip().lower()
        return requested_route if requested_route in self.DESKTOP_ALLOWED_ROUTES else None

    def _looks_like_task_route(self, text: str) -> bool:
        # 这个函数的作用是统一判断消息是否应该进入任务规划链路，兼顾创建任务和查询待办两种场景。
        if any(keyword in text for keyword in self.TASK_QUERY_KEYWORDS):
            return True
        if any(keyword in text for keyword in self.TASK_KEYWORDS):
            return True
        return any(word in text for word in ("什么", "哪些", "安排")) and any(
            hint in text for hint in ("待办", "任务", "工作", "今天")
        )

    @staticmethod
    def _is_at_self(event: UnifiedEvent) -> bool:
        # 这个函数的作用是判断消息是否明确 @ 到机器人自身，兼容 mentions、raw payload 和 CQ 码三种来源。
        self_id = event.self_id
        if not self_id and event.raw_payload:
            raw_self_id = event.raw_payload.get("self_id") or event.raw_payload.get("selfId")
            if raw_self_id is not None:
                self_id = str(raw_self_id)
        if not self_id:
            return False
        if self_id in event.mentions:
            return True
        if RouterService._has_at_segment(event.raw_payload, self_id):
            return True
        return f"[CQ:at,qq={self_id}]" in (event.text or "")

    @staticmethod
    def _has_at_segment(raw_payload: dict, self_id: str) -> bool:
        # 这个函数的作用是从 NapCat 的 array message 结构里识别 @ 机器人的消息分段。
        if not raw_payload:
            return False
        message = raw_payload.get("message")
        if not isinstance(message, list):
            return False
        for segment in message:
            if not isinstance(segment, dict):
                continue
            if segment.get("type") != "at":
                continue
            data = segment.get("data") or {}
            if str(data.get("qq", "")) == self_id:
                return True
        return False
