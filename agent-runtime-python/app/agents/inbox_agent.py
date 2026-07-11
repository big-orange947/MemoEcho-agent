from __future__ import annotations

from collections import Counter
from typing import Any

from app.agents.base import BaseAgent
from app.schemas.results import AgentResult, ToolCallRecord
from app.schemas.tasks import AgentTaskContext


class InboxAgent(BaseAgent):
    name = "inbox"

    async def run(self, task_context: AgentTaskContext, action: str) -> AgentResult:
        # 这个函数的作用是读取当前会话最近消息，并生成可直接回写平台的文本摘要。
        event = task_context.event
        query = {
            "chat_id": event.chat_id,
            "platform": event.platform,
            "chat_type": event.chat_type,
            "limit": 20,
        }
        tool_calls = [ToolCallRecord(tool="get_recent_messages", arguments=query)]
        next_actions: list[str] = []

        try:
            recent_messages = await self._get_tool(task_context, "get_recent_messages").execute(**query)
        except KeyError:
            recent_messages = []
            next_actions.append("get_recent_messages tool is not registered")
        except PermissionError:
            recent_messages = []
            next_actions.append("get_recent_messages tool is not allowed")
        except Exception as exc:
            recent_messages = []
            next_actions.append(f"retry_recent_message_query:{exc}")

        structured_result = self._build_structured_result(event.chat_id, recent_messages)
        reply = self._build_reply(event.sender.name, structured_result)

        return AgentResult(
            task_id=task_context.task_id,
            agent=self.name,
            status="success",
            structured_result=structured_result,
            reply_draft=reply,
            tool_calls=tool_calls,
            next_actions=next_actions,
        )

    def _build_structured_result(self, chat_id: str, messages: list[dict[str, Any]]) -> dict[str, Any]:
        # 这个函数的作用是把原始消息列表整理成稳定的摘要结构，方便 UI 和后续 Agent 继续消费。
        normalized_messages = [message for message in messages if isinstance(message, dict)]
        chat_name = self._derive_chat_name(chat_id, normalized_messages)
        highlight_items = self._collect_highlights(normalized_messages)
        participant_names = self._collect_participants(normalized_messages)
        attachment_count = self._count_attachments(normalized_messages)
        urgent_count = self._count_urgent_messages(normalized_messages)

        return {
            "chat_id": chat_id,
            "chat_name": chat_name,
            "message_count": len(normalized_messages),
            "participants": participant_names,
            "attachment_count": attachment_count,
            "urgent_count": urgent_count,
            "highlights": highlight_items,
        }

    def _build_reply(self, sender_name: str, structured_result: dict[str, Any]) -> str:
        # 这个函数的作用是把结构化摘要转成平台可直接显示的纯文本回复，避免输出 Markdown。
        chat_name = structured_result["chat_name"]
        message_count = structured_result["message_count"]
        highlights = structured_result["highlights"]
        attachment_count = structured_result["attachment_count"]
        urgent_count = structured_result["urgent_count"]

        if not highlights:
            return f"@{sender_name} 我暂时还没有整理到 {chat_name} 最近可摘要的聊天内容。"

        lines = [f"@{sender_name} 我帮你整理了 {chat_name} 最近的消息重点："]
        for index, item in enumerate(highlights, start=1):
            lines.append(f"{index}. {item['sender_name']}：{item['text']}")

        lines.append(f"本次共参考最近 {message_count} 条消息。")
        if urgent_count > 0:
            lines.append(f"其中有 {urgent_count} 条被标记为优先关注消息。")
        if attachment_count > 0:
            lines.append(f"其中有 {attachment_count} 条消息带附件。")
        return "\n".join(lines)

    def _derive_chat_name(self, chat_id: str, messages: list[dict[str, Any]]) -> str:
        # 这个函数的作用是优先使用消息里的 chatName，没有的话再退回 chatId。
        for message in messages:
            chat_name = str(message.get("chatName") or "").strip()
            if chat_name:
                return chat_name
        return chat_id

    def _collect_highlights(self, messages: list[dict[str, Any]]) -> list[dict[str, str]]:
        # 这个函数的作用是提取最近几条最有信息量的消息，并做去重避免摘要刷屏。
        highlights: list[dict[str, str]] = []
        seen_texts: set[str] = set()

        for message in messages:
            text = self._normalize_text(message.get("text"))
            if not text or text in seen_texts:
                continue

            seen_texts.add(text)
            highlights.append(
                {
                    "sender_name": self._normalize_sender_name(message.get("senderName")),
                    "text": text,
                }
            )
            if len(highlights) >= 3:
                break

        return highlights

    def _collect_participants(self, messages: list[dict[str, Any]]) -> list[str]:
        # 这个函数的作用是统计最近消息里出现过的发送者名称，后面 UI 可以直接拿来展示。
        counter: Counter[str] = Counter()
        for message in messages:
            sender_name = self._normalize_sender_name(message.get("senderName"))
            if sender_name != "未知用户":
                counter[sender_name] += 1
        return [name for name, _ in counter.most_common(5)]

    def _count_attachments(self, messages: list[dict[str, Any]]) -> int:
        # 这个函数的作用是统计最近消息中携带附件的条数，便于后面衔接 File Agent。
        return sum(1 for message in messages if isinstance(message.get("attachments"), list) and message.get("attachments"))

    def _count_urgent_messages(self, messages: list[dict[str, Any]]) -> int:
        # 这个函数的作用是统计被 event-center 判断为 urgent 的消息数量。
        return sum(1 for message in messages if str(message.get("dispatchMode") or "").lower() == "urgent")

    def _normalize_text(self, value: Any) -> str:
        # 这个函数的作用是清洗消息文本，去掉空白并限制长度，避免摘要被超长内容拖垮。
        text = str(value or "").strip()
        if not text:
            return ""
        return text if len(text) <= 80 else text[:80] + "..."

    def _normalize_sender_name(self, value: Any) -> str:
        # 这个函数的作用是统一发送者名称的兜底值，避免摘要里出现空名字。
        sender_name = str(value or "").strip()
        return sender_name or "未知用户"
