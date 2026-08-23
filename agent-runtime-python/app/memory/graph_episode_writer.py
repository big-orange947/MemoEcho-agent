"""事件 → Graphiti Episode 异步写入器（P-B：事件接入与节流）。

设计：
- 事件到达后异步调度（fire-and-forget），绝不阻塞聊天主链路。
- 会话级节流：同一 group 的消息先进入缓冲，满足「条数阈值」或「时间窗口」
  才合并为一次 Episode 写入，控制 Graphiti 每次提取的 LLM 调用量。
- 守护循环兜底：首条消息超过时间窗口后即使未满条数也强制写入，避免零星
  消息永远不落图。
- actorType 打标：OWNER/CONTACT/AGENT 的文本事件均可进图（事件记忆完整），
  但提取时仅 OWNER 的强事实会走确认层（P-C），这里不涉及确认逻辑。
- 溯源：批量写入时把 eventIds 汇总进 source_description；显式映射表留到 P-C。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.schemas.events import UnifiedEvent

logger = logging.getLogger(__name__)


@dataclass
class PendingMessage:
    event_id: str
    actor_type: str
    text: str
    time: datetime


@dataclass
class _SessionBuffer:
    messages: list[PendingMessage] = field(default_factory=list)
    first_ts: datetime | None = None


class MemoryGraphEpisodeWriter:
    """把统一事件异步写入 Graphiti 记忆图谱，按会话合并以控制 LLM 提取成本。"""

    def __init__(
        self,
        graph_service: Any,
        *,
        flush_message_count: int = 8,
        flush_window_seconds: float = 60.0,
        guardian_interval_seconds: float = 10.0,
    ) -> None:
        # 这个构造函数的作用是固化节流参数并准备缓冲；guardian 在首次调度时懒启动。
        self.graph_service = graph_service
        self._flush_message_count = max(1, flush_message_count)
        self._flush_window_seconds = max(1.0, flush_window_seconds)
        self._guardian_interval_seconds = max(1.0, guardian_interval_seconds)
        self._buffers: dict[str, _SessionBuffer] = {}
        self._lock = asyncio.Lock()
        self._tasks: set[asyncio.Task] = set()
        self._guardian_task: asyncio.Task | None = None

    # ------------------------------------------------------------------ 入口

    def schedule(self, event: UnifiedEvent) -> bool:
        """校验后异步调度写入；未启用或事件不合格时直接返回 False。"""
        if not self.graph_service.is_enabled:
            return False
        if not self._is_eligible(event):
            return False
        task = asyncio.create_task(self._ingest(event))
        self._tasks.add(task)
        task.add_done_callback(self._finish_task)
        return True

    async def close(self) -> None:
        """取消守护循环并等待在途任务结束；供应用关闭时调用。"""
        if self._guardian_task is not None:
            self._guardian_task.cancel()
            try:
                await self._guardian_task
            except asyncio.CancelledError:
                pass
            self._guardian_task = None
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    # ------------------------------------------------------------- 节流与写入

    @classmethod
    def _is_eligible(cls, event: UnifiedEvent) -> bool:
        # 这个函数的作用是只接受真实的聊天文本事件；桌面命令/空文本/非消息事件不进图。
        if event.platform == "desktop" or event.event_type != "message":
            return False
        text = (event.text or "").strip()
        return len(text) >= 2

    @staticmethod
    def _group_id(event: UnifiedEvent) -> str:
        # 这个函数的作用是构造会话隔离键；self_id 为空时退化为 platform:chatType:chatId。
        self_id = (event.self_id or "").strip()
        prefix = f"{self_id}:" if self_id else ""
        return f"{prefix}{event.platform}:{event.chat_type}:{event.chat_id}"

    @staticmethod
    def _parse_time(event: UnifiedEvent) -> datetime:
        raw = event.sent_at or event.timestamp or ""
        try:
            parsed = datetime.fromisoformat(raw)
        except (TypeError, ValueError):
            return datetime.now(timezone.utc)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    async def _ingest(self, event: UnifiedEvent) -> None:
        group = self._group_id(event)
        message = PendingMessage(
            event_id=event.event_id,
            actor_type=(event.actor_type or "UNKNOWN").upper(),
            text=(event.text or "").strip(),
            time=self._parse_time(event),
        )
        self._ensure_guardian()
        async with self._lock:
            buffer = self._buffers.setdefault(group, _SessionBuffer())
            if buffer.first_ts is None:
                buffer.first_ts = message.time
            buffer.messages.append(message)
            elapsed = (message.time - buffer.first_ts).total_seconds()
            should_flush = len(buffer.messages) >= self._flush_message_count or elapsed >= self._flush_window_seconds
            if not should_flush:
                return
            batch = buffer.messages
            del self._buffers[group]
        await self._flush_batch(group, batch)

    async def _flush_batch(self, group: str, batch: list[PendingMessage]) -> None:
        # 这个函数的作用是把一个会话的一批消息合并成一次 Episode 写入；失败由 graph_service 内部降级。
        lines = [f"[actorType={m.actor_type} {m.time.isoformat()}] {m.text}" for m in batch]
        event_ids = ",".join(m.event_id for m in batch)
        await self.graph_service.write_episode(
            name=f"会话:{group}",
            episode_body="\n".join(lines),
            source_description=f"QQ 消息流 eventIds={event_ids}",
            reference_time=batch[-1].time,
            group_id=group,
        )
        logger.info("memory graph episode flushed: group=%s messages=%d", group, len(batch))

    # ------------------------------------------------------------- 守护循环

    def _ensure_guardian(self) -> None:
        if self._guardian_task is not None and not self._guardian_task.done():
            return
        self._guardian_task = asyncio.create_task(self._guardian_loop())

    async def _guardian_loop(self) -> None:
        # 这个函数的作用是周期性强制写入超过时间窗口的缓冲，防止零星消息长期滞留。
        while True:
            await asyncio.sleep(self._guardian_interval_seconds)
            await self._flush_overdue()

    async def _flush_overdue(self) -> None:
        now = datetime.now(timezone.utc)
        async with self._lock:
            overdue: list[tuple[str, list[PendingMessage]]] = []
            for group, buffer in list(self._buffers.items()):
                if buffer.first_ts is None:
                    continue
                if (now - buffer.first_ts).total_seconds() >= self._flush_window_seconds:
                    overdue.append((group, buffer.messages))
                    del self._buffers[group]
        for group, batch in overdue:
            await self._flush_batch(group, batch)

    # ------------------------------------------------------------- 任务回收

    def _finish_task(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        try:
            task.result()
        except asyncio.CancelledError:
            return
        except Exception as exception:  # noqa: BLE001 - 后台失败不能影响主链路
            logger.warning("记忆图谱事件写入失败，已跳过：error=%s", type(exception).__name__)
