from __future__ import annotations

import unittest

from app.agents.file_agent import FileAgent
from app.schemas.events import Attachment, Sender, UnifiedEvent
from app.schemas.tasks import AgentTaskContext
from app.tools.registry import ToolRegistry
from tool_test_utils import register_test_tool


class DummyExtractFileTextTool:
    def __init__(self) -> None:
        # 这个构造函数的作用是记录工具调用参数，并返回固定的提取结果供断言使用。
        self.calls: list[dict] = []

    async def execute(self, **kwargs):
        # 这个函数的作用是模拟附件文本提取工具，避免测试依赖真实文件解析能力。
        self.calls.append(kwargs)
        return {
            "source": "attachment_metadata",
            "attachment_count": 1,
            "attachment_names": ["项目周报_2026-07-10_18:00提交.docx"],
            "extracted_text": "消息说明：请根据附件整理待办\n附件信息：\n附件1，名称：项目周报_2026-07-10_18:00提交.docx",
        }


class FileAgentTest(unittest.IsolatedAsyncioTestCase):
    async def test_should_extract_attachment_metadata_via_tool(self) -> None:
        registry = ToolRegistry()
        tool = DummyExtractFileTextTool()
        register_test_tool(registry, "extract_file_text", tool)
        agent = FileAgent(registry)

        event = UnifiedEvent(
            eventId="qq:message:private:file-001",
            platform="qq",
            scene="work",
            eventType="message",
            chatType="private",
            chatId="2597164807",
            sender=Sender(id="2597164807", name="freeze", role=None),
            text="请根据附件整理待办",
            attachments=[
                Attachment(
                    fileName="项目周报_2026-07-10_18:00提交.docx",
                    fileType="file",
                    url="https://example.com/report.docx",
                )
            ],
            mentions=[],
            timestamp="2026-07-07T19:00:00+08:00",
            rawPayload={},
        )
        context = AgentTaskContext(task_id="task-file-001", route="file_analysis", event=event)

        result = await agent.run(context, "analyze_attachments")

        self.assertEqual(result.agent, "file")
        self.assertEqual(result.structured_result["attachment_count"], 1)
        self.assertEqual(result.structured_result["analysis_source"], "attachment_metadata")
        self.assertIn("项目周报", result.structured_result["extracted_text"])
        self.assertEqual(tool.calls[0]["message_text"], "请根据附件整理待办")


if __name__ == "__main__":
    unittest.main()
