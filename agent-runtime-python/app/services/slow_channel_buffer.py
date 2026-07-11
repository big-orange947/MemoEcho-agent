from __future__ import annotations

import asyncio
from dataclasses import dataclass
from time import time
from typing import Awaitable, Callable

from app.schemas.events import UnifiedEvent


@dataclass(slots=True)
class BufferedMessage:
    event_id: str
    chat_id: str
    sender_name: str
    text: str
    buffered_at: float


@dataclass(slots=True)
class SlowChannelFlush:
    """慢通道到期后回传事件中心所需的最小摘要数据。"""

    aggregation_key: str
    source_event: UnifiedEvent
    source_event_ids: list[str]
    message_count: int
    summary: str


class SlowChannelBuffer:
    def __init__(
        self,
        window_seconds: int = 600,
        max_messages: int = 10,
        on_flush: Callable[[SlowChannelFlush], Awaitable[None]] | None = None,
    ) -> None:
        # 慢通道用于把一段时间内的普通群消息先攒起来，后面再合并成摘要推送。
        self.window_seconds = window_seconds
        self.max_messages = max_messages
        self.on_flush = on_flush
        self._buffers: dict[str, list[BufferedMessage]] = {}
        self._source_events: dict[str, UnifiedEvent] = {}
        self._timer_tasks: dict[str, asyncio.Task[None]] = {}

    def set_flush_callback(self, callback: Callable[[SlowChannelFlush], Awaitable[None]] | None) -> None:
        """设置窗口到期后的异步回调，使摘要能脱离下一条消息独立入库。"""
        self.on_flush = callback

    def add(
        self,
        aggregation_key: str,
        event: UnifiedEvent,
        window_seconds: int | None = None,
        max_messages: int | None = None,
        allow_threshold_flush: bool = True,
    ) -> dict[str, object]:
        """
        向指定会话的慢通道缓冲区加入消息，并按当前会话设定决定是否产出摘要。

        参数按调用级别覆盖默认值，使不同群聊可以拥有不同的摘要频率，而不需要创建多套缓冲服务。
        """
        now = time()
        effective_window_seconds = window_seconds or self.window_seconds
        effective_max_messages = max_messages or self.max_messages
        buffer = self._buffers.setdefault(aggregation_key, [])
        self._source_events.setdefault(aggregation_key, event)
        message = BufferedMessage(
            event_id=event.event_id,
            chat_id=event.chat_id,
            sender_name=event.sender.name,
            text=(event.text or "").strip(),
            buffered_at=now,
        )
        buffer.append(message)
        self._ensure_timer(aggregation_key, effective_window_seconds)

        should_flush = False
        flush_reason = "none"

        if allow_threshold_flush and len(buffer) >= effective_max_messages:
            should_flush = True
            flush_reason = "max_messages"
        elif allow_threshold_flush and buffer and (now - buffer[0].buffered_at) >= effective_window_seconds:
            # 以第一条消息进入缓冲区的时间作为窗口起点，达到窗口长度就触发汇总。
            should_flush = True
            flush_reason = "window_elapsed"

        if not should_flush:
            return {
                "buffered": True,
                "bufferedCount": len(buffer),
                "flushed": False,
                "flushReason": flush_reason,
                "summaryCandidate": None,
            }

        flushed_messages = self._buffers.pop(aggregation_key, [])
        self._source_events.pop(aggregation_key, None)
        self._cancel_timer(aggregation_key)
        summary = self._build_summary(flushed_messages)
        return {
            "buffered": True,
            "bufferedCount": len(flushed_messages),
            "flushed": True,
            "flushReason": flush_reason,
            "summaryCandidate": summary,
        }

    def _ensure_timer(self, aggregation_key: str, window_seconds: int) -> None:
        """为首次进入缓冲区的会话创建定时任务，已有任务保持原始窗口，避免配置中途改变导致摘要突变。"""
        if aggregation_key in self._timer_tasks:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._timer_tasks[aggregation_key] = loop.create_task(
            self._flush_when_window_elapsed(aggregation_key, window_seconds)
        )

    async def _flush_when_window_elapsed(self, aggregation_key: str, window_seconds: int) -> None:
        """等待窗口结束后自动生成摘要，并通过回调写入事件中心。"""
        try:
            await asyncio.sleep(window_seconds)
            messages = self._buffers.pop(aggregation_key, [])
            source_event = self._source_events.pop(aggregation_key, None)
            if not messages or source_event is None or self.on_flush is None:
                return
            await self.on_flush(SlowChannelFlush(
                aggregation_key=aggregation_key,
                source_event=source_event,
                source_event_ids=[message.event_id for message in messages],
                message_count=len(messages),
                summary=self._build_summary(messages),
            ))
        except asyncio.CancelledError:
            raise
        except Exception:
            # 定时摘要失败不能影响后续消息分发；下一批消息会创建新的缓冲窗口。
            return
        finally:
            self._timer_tasks.pop(aggregation_key, None)

    def _cancel_timer(self, aggregation_key: str) -> None:
        """当数量阈值先触发摘要时取消旧计时任务，防止同一批消息产生重复摘要。"""
        task = self._timer_tasks.pop(aggregation_key, None)
        if task is not None:
            task.cancel()

    def _build_summary(self, messages: list[BufferedMessage]) -> str:
        # 汇总时做一个轻量去重，避免同一句重复刷屏把摘要占满。
        lines = ["过去一段时间群里主要提到："]
        seen_texts: set[str] = set()

        index = 1
        for message in messages:
            text = message.text or "收到一条未携带文本的消息"
            if text in seen_texts:
                continue
            seen_texts.add(text)
            lines.append(f"{index}. {message.sender_name}: {text}")
            index += 1
            if index > 3:
                break

        if len(lines) == 1:
            lines.append("1. 暂无可摘要的文本内容")

        lines.append(f"共缓冲 {len(messages)} 条消息。")
        return "\n".join(lines)
