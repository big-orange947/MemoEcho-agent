from __future__ import annotations

import unittest

from app.clients.llm_service import LlmHttpStatusError
from app.schemas.events import Attachment, Sender, UnifiedEvent
from app.services.media_analysis_service import MediaAnalysisService


class DummyEventCenterClient:
    """记录异步回写调用，避免测试依赖真实 Event Center 服务。"""

    def __init__(self) -> None:
        self.calls: list[tuple[UnifiedEvent, list[dict[str, str]]]] = []

    async def record_media_analysis(self, event: UnifiedEvent, analyses: list[dict[str, str]]) -> None:
        """保存回写参数，供断言检查后台任务输出。"""
        self.calls.append((event, analyses))


class DummyExtractFileTextTool:
    """模拟已有文件工具的文本提取结果。"""

    async def extract(self, **kwargs):
        """返回固定正文，证明异步任务会复用既有文件解析能力。"""
        return {
            "parsed_attachment_count": 1,
            "extracted_text": "项目截止时间为明天上午十点，需要提交文档。",
        }


class DummyVisionLlmClient:
    """模拟具备视觉输入能力的用户模型，避免测试依赖真实图片接口。"""

    def is_enabled(self, model_profile=None) -> bool:
        """声明当前测试模型可用，使图片解析链路进入视觉调用分支。"""
        return True

    async def describe_image(self, **kwargs) -> str:
        """返回可直接用于当前聊天上下文的图片事实描述。"""
        return "图片里写着会议改到明天下午三点"


class FailingVisionLlmClient(DummyVisionLlmClient):
    """模拟供应商拒绝视觉请求，验证状态码不会再退化成笼统的异常类名。"""

    async def describe_image(self, **kwargs) -> str:
        """返回一个已脱敏的模型能力错误，行为与 LlmServiceClient 的转换结果一致。"""
        raise LlmHttpStatusError(400, "InvalidParameter", "Model does not support image input")


class MediaAnalysisServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_should_extract_text_file_and_mark_image_as_unavailable_without_vision_model(self) -> None:
        """文本文件应提取正文；没有视觉模型时图片只能记录可验证元数据。"""
        event_client = DummyEventCenterClient()
        service = MediaAnalysisService(event_client, DummyExtractFileTextTool())
        event = UnifiedEvent(
            eventId="qq:message:private:media-001",
            platform="qq",
            scene="social",
            eventType="message",
            chatType="private",
            chatId="10001",
            sender=Sender(id="10001", name="friend"),
            text="给你两个附件",
            attachments=[
                Attachment(fileId="file-1", fileName="任务说明.docx", fileType="file", url="https://example.com/task.docx"),
                Attachment(fileId="image-1", fileName="photo.png", fileType="image"),
            ],
            timestamp="2026-07-12T10:00:00+08:00",
            rawPayload={},
        )

        await service.analyze_event(event)

        self.assertEqual(1, len(event_client.calls))
        analyses = event_client.calls[0][1]
        self.assertEqual("TEXT_EXTRACTED", analyses[0]["status"])
        self.assertIn("截止时间", analyses[0]["extractedText"])
        self.assertEqual("VISION_UNAVAILABLE", analyses[1]["status"])
        self.assertIn("没有可用的视觉模型", analyses[1]["summary"])

    async def test_should_analyze_image_with_vision_model_and_persist_result(self) -> None:
        """验证图片 data URL 会进入视觉模型，且识别结果可回写为当前回复的可信证据。"""
        event_client = DummyEventCenterClient()
        service = MediaAnalysisService(event_client, DummyExtractFileTextTool(), DummyVisionLlmClient())
        event = UnifiedEvent(
            eventId="qq:message:private:media-vision-001",
            platform="qq",
            scene="social",
            eventType="message",
            chatType="private",
            chatId="10001",
            sender=Sender(id="10001", name="friend"),
            text="",
            attachments=[
                Attachment(
                    fileId="image-vision-1",
                    fileName="notice.png",
                    fileType="image",
                    url="data:image/png;base64,aGVsbG8=",
                )
            ],
            timestamp="2026-07-13T10:00:00+08:00",
            rawPayload={},
        )

        analyses = await service.analyze_event(event)

        self.assertEqual("VISION_ANALYZED", analyses[0]["status"])
        self.assertIn("会议改到明天", analyses[0]["extractedText"])
        self.assertEqual(1, len(event_client.calls))

    async def test_should_preserve_safe_provider_status_when_vision_request_fails(self) -> None:
        """视觉请求失败时接管卡片应显示 HTTP 状态和错误码，而不是只显示 HTTPStatusError。"""
        service = MediaAnalysisService(
            DummyEventCenterClient(),
            DummyExtractFileTextTool(),
            FailingVisionLlmClient(),
        )
        event = UnifiedEvent(
            eventId="qq:message:private:media-vision-failed",
            platform="qq",
            scene="social",
            eventType="message",
            chatType="private",
            chatId="10001",
            sender=Sender(id="10001", name="friend"),
            text="",
            attachments=[
                Attachment(
                    fileId="image-failed-1",
                    fileName="notice.png",
                    fileType="image",
                    url="data:image/png;base64,aGVsbG8=",
                )
            ],
            timestamp="2026-07-13T16:35:00+08:00",
            rawPayload={},
        )

        analyses = await service.analyze_event(event)

        self.assertEqual("VISION_UNAVAILABLE", analyses[0]["status"])
        self.assertIn("HTTP 400", analyses[0]["summary"])
        self.assertIn("InvalidParameter", analyses[0]["summary"])


if __name__ == "__main__":
    unittest.main()
