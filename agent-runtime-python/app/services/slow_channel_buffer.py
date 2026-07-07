from __future__ import annotations

from dataclasses import dataclass
from time import time

from app.schemas.events import UnifiedEvent


@dataclass(slots=True)
class BufferedMessage:
    event_id: str
    chat_id: str
    sender_name: str
    text: str
    buffered_at: float


class SlowChannelBuffer:
    def __init__(self, window_seconds: int = 600, max_messages: int = 10) -> None:
        # 慢通道用于把一段时间内的普通群消息先攒起来，后面再合并成摘要推送。
        self.window_seconds = window_seconds
        self.max_messages = max_messages
        self._buffers: dict[str, list[BufferedMessage]] = {}

    def add(self, aggregation_key: str, event: UnifiedEvent) -> dict[str, object]:
        now = time()
        buffer = self._buffers.setdefault(aggregation_key, [])
        message = BufferedMessage(
            event_id=event.event_id,
            chat_id=event.chat_id,
            sender_name=event.sender.name,
            text=(event.text or "").strip(),
            buffered_at=now,
        )
        buffer.append(message)

        should_flush = False
        flush_reason = "none"

        if len(buffer) >= self.max_messages:
            should_flush = True
            flush_reason = "max_messages"
        elif buffer and (now - buffer[0].buffered_at) >= self.window_seconds:
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
        summary = self._build_summary(flushed_messages)
        return {
            "buffered": True,
            "bufferedCount": len(flushed_messages),
            "flushed": True,
            "flushReason": flush_reason,
            "summaryCandidate": summary,
        }

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
