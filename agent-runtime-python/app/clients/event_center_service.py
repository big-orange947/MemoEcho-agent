from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import httpx

from app.schemas.delegated_tasks import ConversationCandidate, DelegatedTaskCompileResponse
from app.schemas.events import UnifiedEvent
from app.schemas.memories import ExtractedMemoryCandidate, VerifiedMemory
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
        user_id: str | None = None,
        before: str | None = None,
        after: str | None = None,
    ) -> list[dict[str, Any]]:
        # 这个函数的作用是查询某个会话最近的结构化消息，供摘要、补上下文和统一收件箱复用。
        params: dict[str, Any] = {"limit": limit}
        if platform:
            params["platform"] = platform
        if chat_type:
            params["chatType"] = chat_type
        # 时间窗口用于把任务前的背景与任务创建后的执行证据隔离开。
        if before:
            params["before"] = before
        if after:
            params["after"] = after

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.get(
                f"{self.base_url}/internal/conversations/{chat_id}/messages",
                params=params,
                headers=self._runtime_headers(user_id or os.getenv("MEMO_ECHO_RUNTIME_USER_ID") or "default"),
            )
            response.raise_for_status()
            return response.json()

    async def get_active_delegated_task(self, event: UnifiedEvent) -> dict[str, Any] | None:
        """按当前用户和会话键恢复活动委托，使 Python 重启后仍能继续原任务。"""
        user_id = self.resolve_event_user_id(event)
        params = {
            "platform": event.platform,
            "chatType": event.chat_type,
            "chatId": event.chat_id,
        }
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.get(
                f"{self.base_url}/internal/workspace/commands/delegated/active",
                params=params,
                headers=self._runtime_headers(user_id),
            )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("active delegated task response must be an object")
        return payload

    async def claim_delegated_task_event(
        self,
        event: UnifiedEvent,
        task_id: str,
        event_id: str,
        lease_seconds: int = 120,
    ) -> dict[str, Any]:
        """抢占委托事件的执行租约，保证多个 Runtime 实例不会重复处理同一条消息。"""
        normalized_task_id = task_id.strip()
        normalized_event_id = event_id.strip()
        if not normalized_task_id or not normalized_event_id:
            raise ValueError("task_id and event_id are required")
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                (
                    f"{self.base_url}/internal/workspace/commands/delegated/"
                    f"{quote(normalized_task_id, safe='')}/events/claim"
                ),
                json={
                    "eventId": normalized_event_id,
                    "leaseSeconds": max(1, lease_seconds),
                },
                headers=self._runtime_headers(self.resolve_event_user_id(event)),
            )
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict):
            raise ValueError("delegated event claim response must be an object")
        return result

    async def complete_delegated_task_event(
        self,
        event: UnifiedEvent,
        task_id: str,
        event_id: str,
        claim_token: str,
    ) -> None:
        """确认委托事件已经处理完成，使后续重复投递只能读取完成态而不能再次执行。"""
        normalized_task_id = task_id.strip()
        normalized_event_id = event_id.strip()
        normalized_claim_token = claim_token.strip()
        if not normalized_task_id or not normalized_event_id or not normalized_claim_token:
            raise ValueError("task_id, event_id and claim_token are required")
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                (
                    f"{self.base_url}/internal/workspace/commands/delegated/"
                    f"{quote(normalized_task_id, safe='')}/events/complete"
                ),
                json={
                    "eventId": normalized_event_id,
                    "claimToken": normalized_claim_token,
                },
                headers=self._runtime_headers(self.resolve_event_user_id(event)),
            )
        response.raise_for_status()

    async def update_delegated_task_runtime(
        self,
        event: UnifiedEvent,
        task_id: str,
        *,
        status: str,
        progress_summary: str,
        state_json: str,
        last_event_id: str,
        completion_report: str,
    ) -> dict[str, Any]:
        """提交一轮 LangGraph 运行状态；Java 负责终态保护、所有权校验和持久化。"""
        normalized_task_id = task_id.strip()
        if not normalized_task_id:
            raise ValueError("task_id is required")
        payload = {
            "status": status,
            "progressSummary": progress_summary,
            "stateJson": state_json,
            "lastEventId": last_event_id,
            "completionReport": completion_report,
        }
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                f"{self.base_url}/internal/workspace/commands/delegated/{normalized_task_id}/runtime",
                json=payload,
                headers=self._runtime_headers(self.resolve_event_user_id(event)),
            )
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict):
            raise ValueError("delegated task runtime response must be an object")
        return result

    async def list_delegated_task_candidates(self, user_id: str) -> list[ConversationCandidate]:
        """读取 Runtime 可以选择的会话白名单，包含 NapCat 联系人和 Event Center 已知会话。"""
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.get(
                f"{self.base_url}/internal/workspace/commands/delegated/candidates",
                headers=self._runtime_headers(user_id),
            )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise ValueError("delegated task candidates response must be a list")
        return [ConversationCandidate.model_validate(item) for item in payload]

    async def create_delegated_task(
        self,
        user_id: str,
        command: str,
        compilation: DelegatedTaskCompileResponse,
        execution_id: str | None = None,
    ) -> dict[str, Any]:
        """把 LangGraph 编译出的任务提交给 Event Center，由 Java 做白名单校验和持久化。"""
        payload = {
            "command": command,
            "executionId": execution_id,
            "compilation": compilation.model_dump(by_alias=True),
        }
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                f"{self.base_url}/internal/workspace/commands/delegated/runtime-create",
                json=payload,
                headers=self._runtime_headers(user_id),
            )
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict):
            raise ValueError("delegated task create response must be an object")
        return result

    async def list_verified_memories(self, event: UnifiedEvent) -> list[VerifiedMemory]:
        """读取适用于当前事件作用域的已确认长期记忆，候选和过期记录由服务端排除。"""
        user_id = self.resolve_event_user_id(event)
        params = {
            "platform": event.platform,
            "scene": event.scene or "",
            "chatType": event.chat_type,
            "chatId": event.chat_id,
        }
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.get(
                f"{self.base_url}/internal/memories/runtime/verified",
                params=params,
                headers=self._runtime_headers(user_id),
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, list):
                raise ValueError("verified memories response must be a list")
            return [VerifiedMemory.model_validate(item) for item in payload]

    async def create_memory_candidate(
        self,
        event: UnifiedEvent,
        candidate: ExtractedMemoryCandidate,
    ) -> dict[str, Any]:
        """把受信 OWNER 消息提取出的候选交给 Event Center 做证据校验、去重和持久化。"""
        user_id = self.resolve_event_user_id(event)
        payload = {
            "subject": "账号主人",
            "predicate": candidate.predicate,
            "value": candidate.value,
            "scopeType": "CONVERSATION",
            "platform": event.platform,
            "scene": event.scene or "",
            "chatType": event.chat_type,
            "chatId": event.chat_id,
            "sourceEventIds": [event.event_id],
            "sourceActorType": "OWNER",
            "factAuthority": "human_self",
            "confidence": candidate.confidence,
            "expiresAt": candidate.expires_at.isoformat() if candidate.expires_at else None,
        }
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                f"{self.base_url}/internal/memories/runtime/candidates",
                json=payload,
                headers=self._runtime_headers(user_id),
            )
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, dict):
                raise ValueError("memory candidate response must be an object")
            return result

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

    async def request_conversation_task_completion(
        self,
        event: UnifiedEvent,
        profile_id: str,
        summary: str,
        reason: str,
        evidence: list[str],
    ) -> dict[str, Any]:
        """把 Runtime 的任务完成判断提交给 Event Center，等待用户决定是否结束代理。"""
        user_id = self.resolve_event_user_id(event)
        payload = {
            "chatId": event.chat_id,
            "summary": summary,
            "reason": reason,
            "evidence": evidence,
        }
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                f"{self.base_url}/internal/conversation-profiles/{profile_id}/task-completion/request",
                json=payload,
                headers=self._runtime_headers(user_id),
            )
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, dict):
                raise ValueError("task completion response must be an object")
            return result

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

    async def resolve_secure_asset(self, asset_id: str, user_id: str) -> dict[str, Any]:
        """为受信任 Runtime 解密并消费一条安全资产，普通客户端不能调用此接口。"""
        normalized_asset_id = asset_id.strip()
        normalized_user_id = user_id.strip()
        if not normalized_asset_id:
            raise ValueError("asset_id is required")
        if not normalized_user_id:
            raise ValueError("user_id is required")

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                f"{self.base_url}/internal/secure-assets/{normalized_asset_id}/resolve",
                headers=self._runtime_headers(normalized_user_id),
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("secure asset response must be an object")
            return payload

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
            "happened": flush.happened,
            "actionItems": flush.action_items,
            "nextStep": flush.next_step,
            "ownerUserId": self.resolve_event_user_id(event),
            "periodStartedAt": datetime.fromtimestamp(flush.period_started_at, tz=timezone.utc).isoformat(),
            "periodEndedAt": datetime.fromtimestamp(flush.period_ended_at, tz=timezone.utc).isoformat(),
        }
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(f"{self.base_url}/internal/events/digests", json=payload)
            response.raise_for_status()

    async def record_media_analysis(self, event: UnifiedEvent, analyses: list[dict[str, str]]) -> None:
        """将后台附件解析结果回写到原事件，供历史上下文和工作台后续读取。"""
        if not analyses:
            return
        # FastAPI 的 BackgroundTask 在响应发出后立即运行，而 Java 端可能仍在保存原事件。
        # 因此仅对短暂的 404 做有限重试，避免正常竞态导致附件结果丢失。
        for attempt in range(3):
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(
                    f"{self.base_url}/internal/events/{event.event_id}/media-analysis",
                    json={"analyses": analyses},
                    headers=self._runtime_headers(self.resolve_event_user_id(event)),
                )
            if response.status_code != 404 or attempt == 2:
                response.raise_for_status()
                return
            await asyncio.sleep(0.25 * (attempt + 1))

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
        """为 Runtime 到 event-center 的受限请求生成服务认证头，本地默认值与 Java 配置一致。"""
        runtime_token = (os.getenv("EVENT_CENTER_RUNTIME_TOKEN") or "memo-echo-local-runtime-token").strip()
        return {
            "X-Memo-Echo-Runtime-Token": runtime_token,
            "X-Memo-Echo-User-Id": user_id,
        }

    @staticmethod
    def resolve_event_user_id(event: UnifiedEvent) -> str:
        """优先读取桌面事件携带的用户 ID，平台消息则回退到 Runtime 的默认绑定用户。"""
        raw_user_id = event.raw_payload.get("userId") if event.raw_payload else None
        return str(raw_user_id or os.getenv("MEMO_ECHO_RUNTIME_USER_ID") or "default").strip()

    _resolve_event_user_id = resolve_event_user_id
