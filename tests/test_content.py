# -*- coding: utf-8 -*-
"""内容段模型与渲染测试(app/content.py)。

覆盖:
- 文本/@/图片/文件 的渲染形态;
- 未知类型不丢弃(保留 raw_type 与数据);
- 序列化往返(to_dict / from_dict);
- 辅助函数: extract_mentions / has_media。
"""
from __future__ import annotations

from app.content import (
    ContentPart,
    PartType,
    extract_mentions,
    has_media,
    parts_from_dicts,
    parts_to_dicts,
    render,
)


class TestRender:
    def test_text_only(self):
        parts = [ContentPart.text_part("你好")]
        assert render(parts) == "你好"

    def test_mixed_segments(self):
        """@ + 文本 + 图片: 渲染成可读一串。"""
        parts = [
            ContentPart(type=PartType.AT, text="", data={"qq": "12345"}),
            ContentPart.text_part(" 看下这个"),
            ContentPart(type=PartType.IMAGE, text="", data={"file": "x.jpg"}),
        ]
        assert render(parts) == "@12345 看下这个[图片]"

    def test_at_all(self):
        parts = [ContentPart(type=PartType.AT, text="", data={"qq": "all"})]
        assert render(parts) == "@全体成员"

    def test_image_with_summary(self):
        """图片带 summary 时用上描述(信息量更大)。"""
        parts = [ContentPart(type=PartType.IMAGE, text="", data={"summary": "风景照"})]
        assert render(parts) == "[图片:风景照]"

    def test_file_with_name(self):
        parts = [ContentPart(type=PartType.FILE, text="", data={"file": "报告.pdf"})]
        assert render(parts) == "[文件 报告.pdf]"

    def test_unknown_placeholder(self):
        parts = [ContentPart.unknown_part("future_type", {"x": 1})]
        assert render(parts) == "[未知消息]"

    def test_empty_parts(self):
        assert render([]) == ""

    def test_multiple_text_segments_concatenated(self):
        """多段文本按顺序拼接(OneBot 常把一句话拆成多个 text 段)。"""
        parts = [ContentPart.text_part("前半"), ContentPart.text_part("后半")]
        assert render(parts) == "前半后半"


class TestSerialization:
    def test_roundtrip(self):
        parts = [
            ContentPart.text_part("hi"),
            ContentPart(type=PartType.IMAGE, text="", data={"url": "http://x/y.png"}),
        ]
        restored = parts_from_dicts(parts_to_dicts(parts))
        assert len(restored) == 2
        assert restored[0].text == "hi"
        assert restored[1].data["url"] == "http://x/y.png"

    def test_from_dict_tolerates_missing_fields(self):
        part = ContentPart.from_dict({"type": "text"})
        assert part.type == "text"
        assert part.text == ""
        assert part.data == {}


class TestHelpers:
    def test_extract_mentions(self):
        parts = [
            ContentPart(type=PartType.AT, data={"qq": "111"}),
            ContentPart.text_part("hi"),
            ContentPart(type=PartType.AT, data={"qq": "222"}),
            ContentPart(type=PartType.AT, data={"qq": "all"}),  # all 不算具体人
        ]
        assert extract_mentions(parts) == ["111", "222"]

    def test_has_media(self):
        assert has_media([ContentPart(type=PartType.IMAGE)]) is True
        assert has_media([ContentPart.text_part("纯文本")]) is False
        assert has_media([ContentPart.text_part("带图"), ContentPart(type=PartType.FILE)]) is True
