from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import zipfile

from app.tools.extract_file_text_tool import ExtractFileTextTool


class ExtractFileTextToolTest(unittest.IsolatedAsyncioTestCase):
    async def test_should_extract_plain_text_file(self) -> None:
        tool = ExtractFileTextTool()

        with TemporaryDirectory() as temp_dir:
            file_path = Path(temp_dir) / "notice.txt"
            file_path.write_text("今天下午14:00开会，请准备项目演示。", encoding="utf-8")

            result = await tool.extract(
                attachments=[
                    {
                        "file_name": "notice.txt",
                        "local_path": str(file_path),
                    }
                ],
                message_text="请解析附件",
            )

        self.assertEqual(result["parsed_attachment_count"], 1)
        self.assertIn("今天下午14:00开会", result["extracted_text"])

    async def test_should_extract_csv_file(self) -> None:
        tool = ExtractFileTextTool()

        with TemporaryDirectory() as temp_dir:
            file_path = Path(temp_dir) / "tasks.csv"
            file_path.write_text("任务,截止时间\n项目报告,2026-07-10 18:00\n", encoding="utf-8")

            result = await tool.extract(
                attachments=[
                    {
                        "file_name": "tasks.csv",
                        "local_path": str(file_path),
                    }
                ]
            )

        self.assertEqual(result["parsed_attachment_count"], 1)
        self.assertIn("任务 | 截止时间", result["extracted_text"])
        self.assertIn("项目报告 | 2026-07-10 18:00", result["extracted_text"])

    async def test_should_extract_docx_file(self) -> None:
        tool = ExtractFileTextTool()

        with TemporaryDirectory() as temp_dir:
            file_path = Path(temp_dir) / "notice.docx"
            self._write_minimal_docx(
                file_path,
                ["项目周报", "请在2026-07-10 18:00前提交最终版。"],
            )

            result = await tool.extract(
                attachments=[
                    {
                        "file_name": "notice.docx",
                        "local_path": str(file_path),
                    }
                ]
            )

        self.assertEqual(result["parsed_attachment_count"], 1)
        self.assertIn("项目周报", result["extracted_text"])
        self.assertIn("2026-07-10 18:00前提交最终版", result["extracted_text"])

    async def test_should_extract_xlsx_file(self) -> None:
        tool = ExtractFileTextTool()

        with TemporaryDirectory() as temp_dir:
            file_path = Path(temp_dir) / "plan.xlsx"
            self._write_minimal_xlsx(
                file_path,
                [
                    ["任务", "截止时间"],
                    ["项目报告", "2026-07-10 18:00"],
                ],
            )

            result = await tool.extract(
                attachments=[
                    {
                        "file_name": "plan.xlsx",
                        "local_path": str(file_path),
                    }
                ]
            )

        self.assertEqual(result["parsed_attachment_count"], 1)
        self.assertIn("任务 | 截止时间", result["extracted_text"])
        self.assertIn("项目报告 | 2026-07-10 18:00", result["extracted_text"])

    def _write_minimal_docx(self, file_path: Path, paragraphs: list[str]) -> None:
        # 这个辅助函数的作用是生成最小可解析 docx，供单测验证正文提取逻辑。
        document_body = "".join(
            f"<w:p><w:r><w:t>{paragraph}</w:t></w:r></w:p>" for paragraph in paragraphs
        )
        document_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            f"<w:body>{document_body}</w:body>"
            "</w:document>"
        )

        with zipfile.ZipFile(file_path, "w") as archive:
            archive.writestr("[Content_Types].xml", "<?xml version=\"1.0\" encoding=\"UTF-8\"?>")
            archive.writestr("word/document.xml", document_xml)

    def _write_minimal_xlsx(self, file_path: Path, rows: list[list[str]]) -> None:
        # 这个辅助函数的作用是生成最小可解析 xlsx，供单测验证表格读取逻辑。
        shared_strings: list[str] = []
        shared_string_index: dict[str, int] = {}

        def get_shared_index(value: str) -> int:
            if value not in shared_string_index:
                shared_string_index[value] = len(shared_strings)
                shared_strings.append(value)
            return shared_string_index[value]

        row_xml_parts: list[str] = []
        for row_index, row in enumerate(rows, start=1):
            cell_xml_parts: list[str] = []
            for col_index, value in enumerate(row, start=1):
                column_name = chr(64 + col_index)
                shared_index = get_shared_index(value)
                cell_xml_parts.append(
                    f'<c r="{column_name}{row_index}" t="s"><v>{shared_index}</v></c>'
                )
            row_xml_parts.append(f'<row r="{row_index}">{"".join(cell_xml_parts)}</row>')

        shared_strings_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            + "".join(f"<si><t>{value}</t></si>" for value in shared_strings)
            + "</sst>"
        )
        sheet_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f'<sheetData>{"".join(row_xml_parts)}</sheetData>'
            "</worksheet>"
        )

        with zipfile.ZipFile(file_path, "w") as archive:
            archive.writestr("[Content_Types].xml", "<?xml version=\"1.0\" encoding=\"UTF-8\"?>")
            archive.writestr("xl/sharedStrings.xml", shared_strings_xml)
            archive.writestr("xl/worksheets/sheet1.xml", sheet_xml)


if __name__ == "__main__":
    unittest.main()
