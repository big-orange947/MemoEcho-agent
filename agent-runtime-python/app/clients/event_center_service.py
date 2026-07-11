from __future__ import annotations

import os
from typing import Any

import httpx

from app.schemas.events import UnifiedEvent
from app.schemas.model_profiles import UserModelProfileResolveResult
from app.schemas.profiles import ConversationProfileMatchResult
from app.services.slow_channel_buffer import SlowChannelFlush


class EventCenterServiceClient:
    def __init__(self, base_url: str | None = None, timeout_seconds: float = 10.0) -> None:
        # 这个构造函数的作用是确定 event-center-service 的访问地址，方便本地联调和部署切换。
        self.base_url = (base_url or os.getenv("EVENT_CENTER_SERVICE_BASE_URL") or "http://127.0.0.1:8093").rstrip("/")
        self.timeout_seconds = timeout_seconds

    async def list_conversation_messages(
        self,
        chat_id: str,
        platform: str | None = None,
        chat_type: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        # 这个函数的作用是查询某个会话最近的结构化消息，供摘要、补上下文和统一收件箱复用。
        params: dict[str, Any] = {"limit": limit}
        if platform:
            params["platform"] = platform
        if chat_type:
            params["chatType"] = chat_type

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.get(
                f"{self.base_url}/internal/conversations/{chat_id}/messages",
                params=params,
            )
            response.raise_for_status()
            return response.json()

    async def match_conversation_profile(self, event: UnifiedEvent, route: str) -> ConversationProfileMatchResult:
        # 这个函数的作用是把当前消息上下文连同预判 route 一起发给配置中心，让后端匹配最具体的设定集。
        user_id = self._resolve_event_user_id(event)
        payload = {
            "platform": event.platform,
            "accountId": event.self_id or "",
            "scene": event.scene or "",
            "chatType": event.chat_type,
            "chatId": event.chat_id,
            "senderId": event.sender.id,
            "senderRole": event.sender.role or "",
            "route": route,
            "text": event.text or "",
            "atSelf": self._is_at_self(event),
        }

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                f"{self.base_url}/internal/conversation-profiles/match",
                json=payload,
                headers=self._runtime_headers(user_id),
            )
            response.raise_for_status()
            return ConversationProfileMatchResult.model_validate(response.json())

    async def resolve_user_model_profile(
        self,
        route: str,
        user_id: str | None = None,
        profile_id: str | None = None,
    ) -> UserModelProfileResolveResult:
        # 这个函数的作用是请求当前用户在指定 route 下应该使用的模型配置，支持会话显式绑定模型。
        payload = {
            "userId": (user_id or os.getenv("MEMO_ECHO_RUNTIME_USER_ID") or "default").strip(),
            "route": route,
        }
        if profile_id and profile_id.strip():
            payload["profileId"] = profile_id.strip()

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                f"{self.base_url}/internal/user-model-profiles/resolve",
                json=payload,
                headers=self._runtime_headers(payload["userId"]),
            )
            response.raise_for_status()
            return UserModelProfileResolveResult.model_validate(response.json())

    async def publish_slow_channel_digest(self, flush: SlowChannelFlush) -> None:
        """将定时器到期的群聊摘要回传事件中心，生成可直接展示在工作台中的合成事件。"""
        event = flush.source_event
        payload = {
            "platform": event.platform,
            "scene": event.scene or "",
            "chatType": event.chat_type,
            "chatId": event.chat_id,
            "selfId": event.self_id or "",
            "aggregationKey": flush.aggregation_key,
            "sourceEventIds": flush.source_event_ids,
            "messageCount": flush.message_count,
            "summary": flush.summary,
        }
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(f"{self.base_url}/internal/events/digests", json=payload)
            response.raise_for_status()

    @staticmethod
    def _is_at_self(event: UnifiedEvent) -> bool:
        # 这个函数的作用是统一判断当前消息是否明确 @ 到机器人自身。
        if event.self_id and event.self_id in event.mentions:
            return True

        if not event.self_id or not event.raw_payload:
            return False

        message = event.raw_payload.get("message")
        if not isinstance(message, list):
            return False

        for segment in message:
            if not isinstance(segment, dict):
                continue
            if segment.get("type") != "at":
                continue
            data = segment.get("data") or {}
            if str(data.get("qq", "")) == event.self_id:
                return True
        return False

    @staticmethod
    def _runtime_headers(user_id: str) -> dict[str, str]:
        """为 runtime 到 event-center 的受限请求生成服务认证头；未配置令牌时保留本地迁移兼容。"""
        runtime_token = (os.getenv("EVENT_CENTER_RUNTIME_TOKEN") or "").strip()
        if not runtime_token:
            return {}
        return {
            "X-Memo-Echo-Runtime-Token": runtime_token,
            "X-Memo-Echo-User-Id": user_id,
        }

    @staticmethod
    def _resolve_event_user_id(event: UnifiedEvent) -> str:
        """优先读取桌面事件携带的用户 ID，平台消息则回退到 Runtime 的默认绑定用户。"""
        raw_user_id = event.raw_payload.get("userId") if event.raw_payload else None
        return str(raw_user_id or os.getenv("MEMO_ECHO_RUNTIME_USER_ID") or "default").strip()
