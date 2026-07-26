from __future__ import annotations

from app.agents.base import BaseAgent
from app.schemas.results import AgentResult, ToolCallRecord
from app.schemas.tasks import AgentTaskContext


class FileAgent(BaseAgent):
    name = "file"

    async def run(self, task_context: AgentTaskContext, action: str) -> AgentResult:
        # 这个函数的作用是把当前消息中的附件整理成结构化分析结果，供后续 WorkAgent 等继续消费。
        payload = {
            "attachments": [attachment.model_dump() for attachment in task_context.event.attachments],
            "message_text": task_context.event.text or "",
        }
        tool_calls = [ToolCallRecord(tool="extract_file_text", arguments=payload)]
        next_actions: list[str] = []

        try:
            extracted = await self._invoke_tool(task_context, "extract_file_text", payload)
        except KeyError:
            extracted = self._fallback_extract(payload["attachments"], payload["message_text"])
            next_actions.append("extract_file_text tool is not registered")
        except PermissionError:
            extracted = self._fallback_extract(payload["attachments"], payload["message_text"])
            next_actions.append("extract_file_text tool is not allowed")
        except Exception as exc:
            extracted = self._fallback_extract(payload["attachments"], payload["message_text"])
            next_actions.append(f"retry_file_extraction:{exc}")

        structured_result = {
            "attachment_count": extracted["attachment_count"],
            "attachment_names": extracted["attachment_names"],
            "analysis_source": extracted["source"],
            "extracted_text": extracted["extracted_text"],
        }

        return AgentResult(
            task_id=task_context.task_id,
            agent=self.name,
            status="success",
            structured_result=structured_result,
            reply_draft="",
            tool_calls=tool_calls,
            next_actions=next_actions,
        )

    def _fallback_extract(self, attachments: list[dict], message_text: str) -> dict[str, object]:
        # 这个函数的作用是在工具不可用时退回到本地元数据整理，保证附件链路不断。
        attachment_names = [
            str(attachment.get("file_name") or attachment.get("fileName") or "").strip()
            for attachment in attachments
            if str(attachment.get("file_name") or attachment.get("fileName") or "").strip()
        ]

        text_lines: list[str] = []
        if message_text.strip():
            text_lines.append(f"消息说明：{message_text.strip()}")
        if attachment_names:
            text_lines.append(f"附件名称：{'、'.join(attachment_names)}")

        return {
            "source": "fallback_attachment_metadata",
            "attachment_count": len(attachments),
            "attachment_names": attachment_names,
            "extracted_text": "\n".join(text_lines).strip(),
        }
