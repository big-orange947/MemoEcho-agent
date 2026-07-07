from app.schemas.events import UnifiedEvent


class RouterService:
    def route(self, event: UnifiedEvent) -> str:
        text = (event.text or "").lower()
        # 私聊或群里 @ 自己，才会进入需要 agent 明确处理的主链路。
        is_direct_message = event.chat_type == "private" or self._is_at_self(event)

        if not is_direct_message:
            return "message_dispatch"

        if event.attachments:
            return "file_analysis"
        # 这里先做轻量关键词路由，保证没有大模型时也能完成主流程联调。
        if any(keyword in text for keyword in ["today", "schedule", "meeting", "14:00", "deadline", "日程", "会议", "提醒"]):
            return "schedule_extract"
        if any(keyword in text for keyword in [
            "plan", "todo", "task", "work", "submit", "finish",
            "待办", "任务", "完成", "提交", "整理", "汇总", "跟进",
        ]):
            return "task_plan"
        if event.chat_type == "private":
            return "social_reply"
        if any(keyword in text for keyword in ["notice", "welcome", "mute", "announce"]):
            return "group_ops"
        return "chat_summary"

    @staticmethod
    def _is_at_self(event: UnifiedEvent) -> bool:
        # 不同上报格式里，自身账号和 @ 信息的位置可能不一样，
        # 所以这里会同时检查标准字段、raw payload 和 CQ 码文本。
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
        if not raw_payload:
            return False
        # NapCat 的 array message 格式下，@ 信息会拆成独立 segment。
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
