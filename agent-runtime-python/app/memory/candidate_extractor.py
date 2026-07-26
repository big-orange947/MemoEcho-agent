from __future__ import annotations

import asyncio
import json
import logging
import re

from app.clients.event_center_service import EventCenterServiceClient
from app.clients.llm_service import LlmServiceClient
from app.schemas.events import UnifiedEvent
from app.schemas.memories import MemoryCandidateExtraction
from app.schemas.model_profiles import ResolvedUserModelProfile
from app.schemas.profiles import ConversationProfileMatchResult


logger = logging.getLogger(__name__)


class MemoryCandidateExtractor:
    """在用户明确授权后，从 OWNER 真人消息异步提取长期记忆候选。"""

    _MIN_CONFIDENCE = 0.8

    def __init__(
        self,
        event_center_client: EventCenterServiceClient,
        llm_client: LlmServiceClient,
    ) -> None:
        """保存模型与持久化客户端，并跟踪后台任务避免任务对象被提前回收。"""
        self.event_center_client = event_center_client
        self.llm_client = llm_client
        self._tasks: set[asyncio.Task] = set()

    def schedule(
        self,
        event: UnifiedEvent,
        profile_match: ConversationProfileMatchResult | None,
        model_profile: ResolvedUserModelProfile | None,
    ) -> bool:
        """校验授权和消息身份后启动后台提取，不阻塞当前聊天回复。"""
        if not self._is_eligible(event, profile_match, model_profile):
            return False
        task = asyncio.create_task(self.extract_and_store(event, model_profile))
        self._tasks.add(task)
        task.add_done_callback(self._finish_task)
        return True

    async def extract_and_store(
        self,
        event: UnifiedEvent,
        model_profile: ResolvedUserModelProfile | None,
    ) -> list[dict]:
        """调用模型生成结构化事实，并只写入达到候选置信度的结果。"""
        response = await self.llm_client.generate_reply(
            self._build_system_prompt(),
            self._build_user_message(event),
            temperature=0.0,
            model_profile=model_profile,
        )
        extraction = self._parse_response(response)
        stored: list[dict] = []
        for candidate in extraction.candidates[:5]:
            candidate.predicate = candidate.predicate.strip()[:80]
            candidate.value = candidate.value.strip()[:1000]
            if not candidate.predicate or not candidate.value or candidate.confidence < self._MIN_CONFIDENCE:
                continue
            stored.append(await self.event_center_client.create_memory_candidate(event, candidate))
        return stored

    def _is_eligible(
        self,
        event: UnifiedEvent,
        profile_match: ConversationProfileMatchResult | None,
        model_profile: ResolvedUserModelProfile | None,
    ) -> bool:
        """只允许显式授权会话中的 OWNER 文本消息进入提取流程。"""
        profile = profile_match.profile if profile_match and profile_match.active else None
        return bool(
            profile
            and profile.profile_context.memory_policy.extraction_enabled
            and (event.actor_type or "").upper() == "OWNER"
            and event.event_type == "message"
            and len((event.text or "").strip()) >= 2
            and self.llm_client.is_enabled(model_profile)
        )

    @staticmethod
    def _build_system_prompt() -> str:
        """构建保守的事实抽取规则，避免问题、玩笑、意图和他人陈述污染候选库。"""
        return (
            "你是长期记忆候选抽取器，只分析账号主人本人刚发送的一条消息。"
            "仅提取主人明确陈述、未来多轮对话仍有用且相对稳定的本人事实，例如身份、长期偏好、关系、"
            "长期项目或稳定约束。不要提取问题、命令、临时状态、当前话题、玩笑、猜测、模型推断、他人事实、"
            "密码验证码等秘密，也不要借助常识补全消息中没有的信息。"
            "如果消息不能独立证明事实，返回空数组。"
            "只返回 JSON：{\"candidates\":[{\"predicate\":\"属性名\",\"value\":\"事实值\","
            "\"confidence\":0.0,\"expiresAt\":null}]}。confidence 范围 0 到 1。"
        )

    @staticmethod
    def _build_user_message(event: UnifiedEvent) -> str:
        """只发送当前证据消息及最小会话定位，不把联系人或 Agent 输出伪装成主人事实。"""
        return json.dumps(
            {
                "platform": event.platform,
                "chatType": event.chat_type,
                "ownerMessage": (event.text or "").strip(),
                "timestamp": event.timestamp,
            },
            ensure_ascii=False,
        )

    @staticmethod
    def _parse_response(raw_response: str) -> MemoryCandidateExtraction:
        """解析模型 JSON；兼容 Markdown 代码围栏，但拒绝无法结构化的自由文本。"""
        normalized = raw_response.strip()
        normalized = re.sub(r"^```(?:json)?\s*", "", normalized, flags=re.IGNORECASE)
        normalized = re.sub(r"\s*```$", "", normalized)
        payload = json.loads(normalized)
        return MemoryCandidateExtraction.model_validate(payload)

    def _finish_task(self, task: asyncio.Task) -> None:
        """回收后台任务并记录失败摘要，提取失败不能影响原消息处理。"""
        self._tasks.discard(task)
        try:
            task.result()
        except asyncio.CancelledError:
            return
        except Exception as exception:
            logger.warning("长期记忆候选提取失败，已跳过：error=%s", type(exception).__name__)
