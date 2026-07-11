from __future__ import annotations

from io import BytesIO
import csv
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from xml.etree import ElementTree as ET
import zipfile

import httpx

from app.tools.base import BaseTool


class ExtractFileTextTool(BaseTool):
    name = "extract_file_text"

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        # 这个函数的作用是批量解析附件内容，并把不同格式的结果统一整理成一段文本供后续 Agent 使用。
        attachments = kwargs.get("attachments") or []
        message_text = str(kwargs.get("message_text") or "").strip()

        attachment_names: list[str] = []
        extracted_blocks: list[str] = []
        parsed_count = 0

        for index, attachment in enumerate(attachments, start=1):
            result = await self._extract_single_attachment(index, attachment)
            if result["file_name"]:
                attachment_names.append(result["file_name"])
            if result["text"]:
                parsed_count += 1
                extracted_blocks.append(result["text"])

        sections: list[str] = []
        if message_text:
            sections.append(f"消息说明：{message_text}")
        if extracted_blocks:
            sections.append("附件解析结果：")
            sections.extend(extracted_blocks)
        elif attachment_names:
            sections.append(f"附件名称：{'、'.join(attachment_names)}")

        return {
            "source": "parsed_attachment_content" if parsed_count > 0 else "attachment_metadata",
            "attachment_count": len(attachments),
            "attachment_names": attachment_names,
            "parsed_attachment_count": parsed_count,
            "extracted_text": "\n".join(sections).strip(),
        }

    async def _extract_single_attachment(self, index: int, attachment: dict[str, Any]) -> dict[str, str]:
        # 这个函数的作用是解析单个附件，统一返回“附件标题 + 解析文本”结构。
        file_name = self._pick_string(attachment, "file_name", "fileName", "name")
        file_type = self._pick_string(attachment, "file_type", "fileType", "type")
        source = self._pick_string(attachment, "local_path", "localPath", "path", "url")
        extension = self._detect_extension(file_name, file_type, source)

        if not source:
            return {
                "file_name": file_name,
                "text": self._build_attachment_block(index, file_name, "未提供可读取的附件地址。"),
            }

        try:
            data = await self._load_attachment_bytes(source)
            parsed_text = self._parse_bytes_by_extension(data, extension)
        except Exception as exc:
            parsed_text = f"解析失败：{exc}"

        return {
            "file_name": file_name,
            "text": self._build_attachment_block(index, file_name, parsed_text),
        }

    async def _load_attachment_bytes(self, source: str) -> bytes:
        # 这个函数的作用是按来源类型读取附件字节，支持本地路径、file URL 和 HTTP URL。
        parsed = urlparse(source)
        if parsed.scheme in {"http", "https"}:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(source)
                response.raise_for_status()
                return response.content

        if parsed.scheme == "file":
            return Path(parsed.path).read_bytes()

        return Path(source).expanduser().read_bytes()

    def _parse_bytes_by_extension(self, data: bytes, extension: str) -> str:
        # 这个函数的作用是根据附件扩展名选择解析策略，并把结果裁剪成适合消息上下文的长度。
        parser_map = {
            ".txt": self._parse_plain_text,
            ".md": self._parse_plain_text,
            ".log": self._parse_plain_text,
            ".csv": self._parse_csv,
            ".json": self._parse_json,
            ".docx": self._parse_docx,
            ".xlsx": self._parse_xlsx,
            ".pdf": self._parse_pdf,
        }

        parser = parser_map.get(extension, self._parse_plain_text)
        parsed_text = parser(data)
        normalized = " ".join(parsed_text.split())
        if not normalized:
            return "解析完成，但没有提取到可用文本。"
        return normalized if len(normalized) <= 500 else normalized[:500].rstrip() + "..."

    def _parse_plain_text(self, data: bytes) -> str:
        # 这个函数的作用是解析纯文本类文件，优先使用 UTF-8，失败时再回退到常见编码。
        for encoding in ("utf-8", "utf-8-sig", "gbk", "gb18030"):
            try:
                return data.decode(encoding)
            except UnicodeDecodeError:
                continue
        return data.decode("utf-8", errors="ignore")

    def _parse_csv(self, data: bytes) -> str:
        # 这个函数的作用是提取 CSV 前几行内容，方便快速判断表格里是什么数据。
        text = self._parse_plain_text(data)
        reader = csv.reader(text.splitlines())
        rows = []
        for index, row in enumerate(reader):
            rows.append(" | ".join(cell.strip() for cell in row if cell is not None))
            if index >= 4:
                break
        return "\n".join(row for row in rows if row.strip())

    def _parse_json(self, data: bytes) -> str:
        # 这个函数的作用是把 JSON 格式化成可读文本，避免原始压缩 JSON 难以直接消费。
        obj = json.loads(self._parse_plain_text(data))
        return json.dumps(obj, ensure_ascii=False, indent=2)

    def _parse_docx(self, data: bytes) -> str:
        # 这个函数的作用是从 docx 包内读取正文 XML，并提取文档里的文字内容。
        with zipfile.ZipFile(BytesIO(data)) as archive:
            document_xml = archive.read("word/document.xml")

        root = ET.fromstring(document_xml)
        namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        texts = [node.text for node in root.findall(".//w:t", namespace) if node.text]
        return "\n".join(texts)

    def _parse_xlsx(self, data: bytes) -> str:
        # 这个函数的作用是读取 xlsx 的共享字符串和工作表内容，提取前几行单元格文本。
        with zipfile.ZipFile(BytesIO(data)) as archive:
            shared_strings = self._read_shared_strings(archive)
            sheet_names = sorted(
                name for name in archive.namelist() if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")
            )
            row_lines: list[str] = []
            for sheet_name in sheet_names[:2]:
                row_lines.extend(self._read_sheet_rows(archive.read(sheet_name), shared_strings))
                if len(row_lines) >= 6:
                    break
        return "\n".join(row_lines[:6])

    def _read_shared_strings(self, archive: zipfile.ZipFile) -> list[str]:
        # 这个函数的作用是读取 xlsx 共享字符串表，供后续解析单元格引用值使用。
        if "xl/sharedStrings.xml" not in archive.namelist():
            return []

        root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
        namespace = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        values: list[str] = []
        for item in root.findall(".//s:si", namespace):
            parts = [node.text or "" for node in item.findall(".//s:t", namespace)]
            values.append("".join(parts))
        return values

    def _read_sheet_rows(self, sheet_xml: bytes, shared_strings: list[str]) -> list[str]:
        # 这个函数的作用是读取单个工作表前几行内容，并把共享字符串编号还原成真实文本。
        root = ET.fromstring(sheet_xml)
        namespace = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        row_lines: list[str] = []

        for row in root.findall(".//s:row", namespace):
            cell_values: list[str] = []
            for cell in row.findall("s:c", namespace):
                cell_type = cell.attrib.get("t", "")
                value_node = cell.find("s:v", namespace)
                inline_node = cell.find("s:is/s:t", namespace)

                if inline_node is not None and inline_node.text:
                    cell_values.append(inline_node.text)
                    continue
                if value_node is None or value_node.text is None:
                    continue
                raw_value = value_node.text
                if cell_type == "s" and raw_value.isdigit():
                    index = int(raw_value)
                    cell_values.append(shared_strings[index] if index < len(shared_strings) else raw_value)
                else:
                    cell_values.append(raw_value)

            if cell_values:
                row_lines.append(" | ".join(cell_values))
            if len(row_lines) >= 6:
                break

        return row_lines

    def _parse_pdf(self, data: bytes) -> str:
        # 这个函数的作用是尽量从 PDF 提取文本；如果缺少解析库，就明确返回依赖提示。
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise RuntimeError("当前环境未安装 pypdf，暂时无法解析 PDF。") from exc

        reader = PdfReader(BytesIO(data))
        texts = [page.extract_text() or "" for page in reader.pages[:5]]
        return "\n".join(texts)

    def _build_attachment_block(self, index: int, file_name: str, text: str) -> str:
        # 这个函数的作用是把单个附件的解析结果包装成统一段落格式，便于后续模型或规则消费。
        title = file_name or f"附件{index}"
        return f"附件{index}：{title}\n{text.strip()}"

    def _pick_string(self, attachment: dict[str, Any], *field_names: str) -> str:
        # 这个函数的作用是兼容不同命名风格的附件字段，优先取第一个非空字符串。
        for field_name in field_names:
            value = str(attachment.get(field_name) or "").strip()
            if value:
                return value
        return ""

    def _detect_extension(self, file_name: str, file_type: str, source: str) -> str:
        # 这个函数的作用是综合文件名、文件类型和来源地址，推断解析所需的扩展名。
        for candidate in (file_name, source):
            suffix = Path(candidate).suffix.lower()
            if suffix:
                return suffix

        normalized_type = file_type.lower().strip()
        if normalized_type in {"txt", "md", "csv", "json", "docx", "xlsx", "pdf"}:
            return f".{normalized_type}"
        return ".txt"
