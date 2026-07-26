from __future__ import annotations

import asyncio
import base64
import logging
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from app.clients.event_center_service import EventCenterServiceClient
from app.clients.llm_service import LlmHttpStatusError, LlmServiceClient
from app.schemas.events import Attachment, UnifiedEvent
from app.schemas.model_profiles import ResolvedUserModelProfile
from app.tools.extract_file_text_tool import ExtractFileTextTool


logger = logging.getLogger(__name__)


class MediaAnalysisService:
    """
    异步处理实时附件。

    文本型文件会使用现有文件解析工具提取正文；图片、语音和视频只在未配置
    专用视觉或转写能力时记录可验证的元数据，避免模型或规则凭空描述媒体内容。
    """

    _TEXTUAL_EXTENSIONS = {".txt", ".md", ".log", ".csv", ".json", ".docx", ".xlsx", ".pdf"}
    _MAX_IMAGE_BYTES = 8 * 1024 * 1024

    def __init__(
        self,
        event_center_client: EventCenterServiceClient,
        extract_file_text_tool: ExtractFileTextTool,
        llm_client: LlmServiceClient | None = None,
    ) -> None:
        # 这个构造函数的作用是复用既有文件解析工具，并把结果回写到 Event Center。
        self.event_center_client = event_center_client
        self.extract_file_text_tool = extract_file_text_tool
        self.llm_client = llm_client

    async def analyze_event(
        self,
        event: UnifiedEvent,
        model_profile: ResolvedUserModelProfile | None = None,
    ) -> list[dict[str, str]]:
        """并发分析一条实时消息中的附件；任何单个附件失败都不能影响其他附件。"""
        if not event.attachments:
            return []

        analyses = await asyncio.gather(
            *(self._analyze_attachment(attachment, model_profile) for attachment in event.attachments),
            return_exceptions=True,
        )
        normalized_analyses: list[dict[str, str]] = []
        for attachment, analysis in zip(event.attachments, analyses, strict=True):
            if isinstance(analysis, Exception):
                logger.warning("附件异步解析失败：eventId=%s, file=%s, error=%s", event.event_id, attachment.file_name, analysis)
                normalized_analyses.append(self._failed_analysis(attachment, type(analysis).__name__))
            else:
                normalized_analyses.append(analysis)

        try:
            await self.event_center_client.record_media_analysis(event, normalized_analyses)
            logger.info("附件异步解析已回写：eventId=%s, attachments=%d", event.event_id, len(normalized_analyses))
        except Exception as exception:
            # 回写失败只影响后续上下文增强，不能让已完成的主回复链路失败。
            logger.warning("附件异步解析回写失败：eventId=%s, error=%s", event.event_id, exception)
        return normalized_analyses

    async def _analyze_attachment(
        self,
        attachment: Attachment,
        model_profile: ResolvedUserModelProfile | None,
    ) -> dict[str, str]:
        """按附件类型选择文本提取或元数据探测策略，并返回可持久化的简短结果。"""
        normalized_type = self._normalize_type(attachment)
        if self._is_textual_file(attachment, normalized_type):
            return await self._extract_textual_file(attachment, normalized_type)
        if normalized_type == "image":
            return await self._analyze_image(attachment, model_profile)

        metadata = await self._probe_metadata(attachment)
        labels = {
            "image": "图片已接收并完成元数据读取",
            "audio": "语音已接收",
            "video": "视频已接收",
        }
        summary = labels.get(normalized_type, "附件已接收，暂不支持提取此格式的正文")
        if metadata:
            summary = f"{summary}：{metadata}"
        return self._analysis(attachment, normalized_type, "METADATA_READY", summary)

    async def _analyze_image(
        self,
        attachment: Attachment,
        model_profile: ResolvedUserModelProfile | None,
    ) -> dict[str, str]:
        """读取图片并调用用户配置的视觉模型；无法验证图像内容时只返回明确的不可用状态。"""
        if self.llm_client is None or not self.llm_client.is_enabled(model_profile):
            return self._analysis(
                attachment,
                "image",
                "VISION_UNAVAILABLE",
                "图片已收到，但当前没有可用的视觉模型，不能判断图片内容",
            )

        try:
            image_data_url = await self._load_image_data_url(attachment)
            if not image_data_url:
                return self._analysis(
                    attachment,
                    "image",
                    "VISION_UNAVAILABLE",
                    "图片已收到，但无法读取可供视觉模型分析的图片数据",
                )
            description = await self.llm_client.describe_image(
                system_prompt=(
                    "你是图片信息提取器。只描述图片中可以直接观察到的文字、对象、场景和动作。"
                    "先判断它是否明显属于表情包、梗图或聊天贴纸；如果属于，描述中必须明确写出‘表情包’。"
                    "不得猜测身份、地点、金额、意图或图片外事实。用不超过 120 字的中文返回。"
                ),
                image_data_url=image_data_url,
                user_message="请提取这张聊天图片中与回复有关的可见信息。",
                model_profile=model_profile,
            )
            return self._analysis(
                attachment,
                "image",
                "VISION_ANALYZED",
                "图片内容已由视觉模型识别",
                self._compact_text(description)[:500],
            )
        except LlmHttpStatusError as exception:
            # 供应商状态码和脱敏说明能直接区分模型选错、额度、鉴权和图片参数问题。
            logger.info(
                "图片视觉理解不可用，已降级：file=%s, status=%s, code=%s, detail=%s",
                attachment.file_name,
                exception.status_code,
                exception.error_code or "unknown",
                exception.detail or "未返回错误说明",
            )
            return self._analysis(
                attachment,
                "image",
                "VISION_UNAVAILABLE",
                self._vision_http_error_summary(exception),
            )
        except Exception as exception:
            logger.info("图片视觉理解不可用，已降级：file=%s, error=%s", attachment.file_name, type(exception).__name__)
            return self._analysis(
                attachment,
                "image",
                "VISION_UNAVAILABLE",
                f"图片已收到，但视觉模型未能完成识别（{type(exception).__name__}）",
            )

    async def _load_image_data_url(self, attachment: Attachment) -> str:
        """将 QQ 图片 URL 或本地文件转换为受大小限制的 data URL，避免把临时外链直接暴露给模型。"""
        source = (attachment.url or "").strip()
        if source.startswith("data:image/"):
            return source if len(source.encode("utf-8")) <= self._MAX_IMAGE_BYTES * 2 else ""
        if not source:
            return ""

        parsed = urlparse(source)
        content = b""
        content_type = ""
        if parsed.scheme in {"http", "https"}:
            async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
                response = await client.get(source)
                response.raise_for_status()
                content = response.content
                content_type = response.headers.get("content-type", "").split(";", maxsplit=1)[0].strip()
        else:
            path = Path(parsed.path if parsed.scheme == "file" else source).expanduser()
            if not path.is_file():
                return ""
            content = await asyncio.to_thread(path.read_bytes)
            content_type = self._guess_image_content_type(path.suffix)

        if not content or len(content) > self._MAX_IMAGE_BYTES:
            return ""
        if not content_type.startswith("image/"):
            content_type = self._guess_image_content_type(Path(parsed.path).suffix)
        if not content_type.startswith("image/"):
            return ""
        encoded = base64.b64encode(content).decode("ascii")
        return f"data:{content_type};base64,{encoded}"

    @staticmethod
    def _guess_image_content_type(extension: str) -> str:
        """根据常见文件扩展名补全图片 MIME 类型，供没有 Content-Type 的 QQ 临时文件使用。"""
        return {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".gif": "image/gif",
            ".webp": "image/webp",
            ".bmp": "image/bmp",
        }.get(extension.lower(), "")

    async def _extract_textual_file(self, attachment: Attachment, normalized_type: str) -> dict[str, str]:
        """调用既有文件工具提取文本，避免在实时回复主链路中等待下载和格式解析。"""
        result = await self.extract_file_text_tool.extract(
            attachments=[attachment.model_dump()],
            message_text="",
        )
        extracted_text = str(result.get("extracted_text") or "").strip()
        parsed_count = int(result.get("parsed_attachment_count") or 0)
        if parsed_count > 0 and extracted_text:
            return self._analysis(
                attachment,
                normalized_type,
                "TEXT_EXTRACTED",
                "已提取文件正文摘要",
                self._compact_text(extracted_text),
            )

        return self._analysis(
            attachment,
            normalized_type,
            "METADATA_READY",
            "文件已接收，但没有提取到可用正文",
        )

    async def _probe_metadata(self, attachment: Attachment) -> str:
        """读取附件大小和 Content-Type，不下载图片、语音或视频的完整二进制内容。"""
        source = (attachment.url or "").strip()
        if not source:
            return "未提供可访问地址"

        parsed = urlparse(source)
        if parsed.scheme in {"http", "https"}:
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                response = await client.head(source)
                if response.status_code >= 400:
                    return f"地址探测失败（HTTP {response.status_code}）"
                content_type = response.headers.get("content-type", "").split(";", maxsplit=1)[0].strip()
                content_length = response.headers.get("content-length", "").strip()
                parts = []
                if content_type:
                    parts.append(content_type)
                if content_length.isdigit():
                    parts.append(self._format_size(int(content_length)))
                return "，".join(parts) or "远程地址可访问"

        path = Path(parsed.path if parsed.scheme == "file" else source).expanduser()
        if path.exists() and path.is_file():
            return self._format_size(path.stat().st_size)
        return "本地附件地址不可访问"

    @classmethod
    def _is_textual_file(cls, attachment: Attachment, normalized_type: str) -> bool:
        """判断附件是否应该走文本提取，而不是只做媒体元数据探测。"""
        if normalized_type != "file":
            return False
        file_name = (attachment.file_name or "").lower()
        source_path = urlparse(attachment.url or "").path.lower()
        return Path(file_name).suffix in cls._TEXTUAL_EXTENSIONS or Path(source_path).suffix in cls._TEXTUAL_EXTENSIONS

    @staticmethod
    def _normalize_type(attachment: Attachment) -> str:
        """兼容 NapCat 的 record 命名，将附件类型归一化为 Runtime 的媒体类别。"""
        file_type = (attachment.file_type or "").lower().strip()
        if file_type in {"record", "audio"}:
            return "audio"
        if file_type in {"image", "video", "file"}:
            return file_type
        return "unknown"

    @staticmethod
    def _analysis(
        attachment: Attachment,
        file_type: str,
        status: str,
        summary: str,
        extracted_text: str = "",
    ) -> dict[str, str]:
        """统一构造回写 DTO，确保后台任务不会泄漏文件二进制内容或完整远程地址。"""
        return {
            "attachmentId": attachment.file_id or "",
            "fileName": attachment.file_name or "",
            "fileType": file_type,
            "status": status,
            "summary": summary,
            "extractedText": extracted_text,
        }

    def _failed_analysis(self, attachment: Attachment, error_type: str) -> dict[str, str]:
        """将后台异常降级为可见状态，方便后续重试而不把底层异常暴露给聊天对象。"""
        return self._analysis(
            attachment,
            self._normalize_type(attachment),
            "FAILED",
            f"附件解析失败（{error_type}），可稍后重试",
        )

    @staticmethod
    def _vision_http_error_summary(exception: LlmHttpStatusError) -> str:
        """生成适合接管卡片展示的简短视觉错误，不暴露请求体、密钥或图片数据。"""
        provider_reason = exception.error_code or exception.detail
        if provider_reason:
            return f"图片已收到，但视觉模型调用失败（HTTP {exception.status_code}：{provider_reason[:120]}）"
        return f"图片已收到，但视觉模型调用失败（HTTP {exception.status_code}）"

    @staticmethod
    def _compact_text(value: str) -> str:
        """将文件正文压缩为适合后续对话上下文使用的短摘要。"""
        normalized = " ".join(value.split())
        return normalized[:800].rstrip() + ("..." if len(normalized) > 800 else "")

    @staticmethod
    def _format_size(size: int) -> str:
        """把字节数转换为便于工作台展示的容量文本。"""
        if size < 1024:
            return f"{size} B"
        if size < 1024 * 1024:
            return f"{size / 1024:.1f} KB"
        return f"{size / (1024 * 1024):.1f} MB"
