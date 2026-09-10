# =============================================================================
# content.py - 平台无关的「消息内容段」模型
# -----------------------------------------------------------------------------
# 为什么需要这一层:
#   聊天平台(QQ/微信/...)的消息不是纯文本,而是**有序的内容段数组**。
#   例如"@km 看下[图片]"在 OneBot 里是:
#       [{"type":"at","data":{"qq":"123"}}, {"type":"text","data":{"text":" 看下"}},
#        {"type":"image","data":{"file":"x.jpg","url":"https://..."}}]
#
#   如果直接把整段数组字符串化(旧实现的问题),会得到无意义的一坨文本;
#   如果只取 text 段,又会丢失"@了谁""发的是哪张图"这类关键语义。
#
# 设计:
#   - ContentPart: 单个内容段(类型 + 可读渲染文本 + 原始数据);
#   - render():    把内容段列表拼成**可读文本**,供 LLM 理解与落库展示;
#   - 原始 data 始终保留 —— 上层要做高级处理(下载图片、引用回复)时不用重新解析。
#
# 关键约定:
#   - 未知类型绝不丢弃: 归为 "unknown" 并给出占位文本,保证"收到过什么"可追溯;
#   - 渲染文本是**给人/模型看的**,不是协议数据 —— 协议数据在 data 里。
# =============================================================================

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable


# ---------------------------------------------------------------------------
# 内容段类型常量
# 与 OneBot 11 的消息段类型对齐(见 NapCat 文档 onebot/segment),
# 未来接入其他平台时,由各自的解析器映射到这套统一类型。
# ---------------------------------------------------------------------------
class PartType:
    """内容段类型(字符串常量,便于直接与平台原始 type 比对)。"""

    TEXT = "text"        # 纯文本
    AT = "at"            # @某人(qq="all" 表示 @全体)
    REPLY = "reply"      # 引用回复(指向某条消息)
    FACE = "face"        # QQ 内置表情
    MFACE = "mface"      # 商城表情(接收时平台常转为 image)
    DICE = "dice"        # 骰子
    RPS = "rps"          # 石头剪刀布
    POKE = "poke"        # 戳一戳
    IMAGE = "image"      # 图片(有 url / file)
    RECORD = "record"    # 语音
    VIDEO = "video"      # 视频
    FILE = "file"        # 文件
    JSON = "json"        # JSON 卡片(分享链接等)
    FORWARD = "forward"  # 合并转发
    MUSIC = "music"      # 音乐分享(仅发送)
    UNKNOWN = "unknown"  # 未识别类型(保留原始数据)


# ---------------------------------------------------------------------------
# 渲染占位符: 非文本段在"可读文本"里的呈现方式
# 目的: 让 LLM 知道"这里有个东西但不是文本",而不是凭空编造内容。
# ---------------------------------------------------------------------------
_PLACEHOLDERS: dict[str, str] = {
    PartType.REPLY: "[回复]",
    PartType.FACE: "[表情]",
    PartType.MFACE: "[表情]",
    PartType.DICE: "[骰子]",
    PartType.RPS: "[猜拳]",
    PartType.POKE: "[戳一戳]",
    PartType.IMAGE: "[图片]",
    PartType.RECORD: "[语音]",
    PartType.VIDEO: "[视频]",
    PartType.FILE: "[文件]",
    PartType.JSON: "[卡片]",
    PartType.FORWARD: "[合并转发]",
    PartType.MUSIC: "[音乐]",
    PartType.UNKNOWN: "[未知消息]",
}


@dataclass
class ContentPart:
    """一个消息内容段。

    字段说明:
      type: 段类型(见 PartType)。未知类型统一为 "unknown"。
      text: 可读渲染文本。文本段是原文;非文本段是占位符(如 "[图片]")。
      data: 平台原始 data 字段,原样保留(图片 url、@ 的 QQ 号等都在这里)。
    """

    type: str
    text: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    # ---------------------------------------------------------------- 构造便捷方法
    @classmethod
    def text_part(cls, text: str) -> "ContentPart":
        """构造文本段。"""
        return cls(type=PartType.TEXT, text=text, data={"text": text})

    @classmethod
    def unknown_part(cls, raw_type: str, data: dict[str, Any] | None = None) -> "ContentPart":
        """构造未知类型段(保留原始类型名与数据,便于以后补支持)。"""
        return cls(
            type=PartType.UNKNOWN,
            text=_PLACEHOLDERS[PartType.UNKNOWN],
            data={"raw_type": raw_type, **(data or {})},
        )

    # ---------------------------------------------------------------- 序列化
    def to_dict(self) -> dict[str, Any]:
        """转成可 JSON 序列化的字典(落库 / 传输用)。"""
        return {"type": self.type, "text": self.text, "data": self.data}

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ContentPart":
        """从字典还原(读取落库数据时用)。"""
        return cls(
            type=str(payload.get("type") or PartType.UNKNOWN),
            text=str(payload.get("text") or ""),
            data=payload.get("data") or {},
        )


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------
def render(parts: Iterable[ContentPart], *, separator: str = "") -> str:
    """把内容段列表渲染成可读文本。

    规则:
      - 文本段原样拼接;
      - @ 段渲染为 "@QQ号"(或 "@全体成员");
      - 其他段用固定占位符(见 _PLACEHOLDERS),不编造内容;
      - 空段跳过;结果两端去空白。

    separator: 段之间的连接符,默认空串(聊天文本通常紧挨着)。
               若希望段之间更易读,可传 "" 以外的值(如 " ")。
    """
    rendered: list[str] = []
    for part in parts:
        piece = _render_one(part)
        if piece:
            rendered.append(piece)
    return separator.join(rendered).strip()


def _render_one(part: ContentPart) -> str:
    """渲染单个内容段。"""
    # 文本段与未知段都直接用自己的 text
    if part.type == PartType.TEXT:
        return part.text

    if part.type == PartType.AT:
        qq = str(part.data.get("qq") or "")
        if qq == "all":
            return "@全体成员"
        return f"@{qq}" if qq else "@"

    if part.type == PartType.FILE:
        # 文件名比占位符更有信息量(如 "[文件 报告.pdf]")
        name = str(part.data.get("file") or part.data.get("name") or "")
        return f"[文件 {name}]" if name else _PLACEHOLDERS[PartType.FILE]

    if part.type == PartType.IMAGE:
        # 图片有时带 summary(平台给的描述),有则用上,便于模型理解
        summary = str(part.data.get("summary") or "")
        return f"[图片:{summary}]" if summary else _PLACEHOLDERS[PartType.IMAGE]

    # 其余类型: 只要占位符(未知类型也走这里,保证不丢"有这么一段"的事实)
    return part.text or _PLACEHOLDERS.get(part.type, _PLACEHOLDERS[PartType.UNKNOWN])


# ---------------------------------------------------------------------------
# 工具函数: 段列表 ↔ 字典列表(落库/传输)
# ---------------------------------------------------------------------------
def parts_to_dicts(parts: Iterable[ContentPart]) -> list[dict[str, Any]]:
    """内容段列表 → 字典列表(存进 messages.raw_json / events.payload 时用)。"""
    return [part.to_dict() for part in parts]


def parts_from_dicts(payloads: Iterable[dict[str, Any]]) -> list[ContentPart]:
    """字典列表 → 内容段列表(读回落库数据时用)。"""
    return [ContentPart.from_dict(item) for item in payloads]


def extract_mentions(parts: Iterable[ContentPart]) -> list[str]:
    """取出所有被 @ 的 QQ 号(不含 "all")。

    用途: 群聊场景判断"是否 @ 了机器人";或作为实体信息喂给记忆层。
    """
    return [
        str(part.data.get("qq"))
        for part in parts
        if part.type == PartType.AT and part.data.get("qq") and part.data.get("qq") != "all"
    ]


def has_media(parts: Iterable[ContentPart]) -> bool:
    """判断内容里是否含媒体(图片/语音/视频/文件)。

    用途: 需要下载媒体或走多模态理解时的快速判断。
    """
    media_types = {PartType.IMAGE, PartType.RECORD, PartType.VIDEO, PartType.FILE}
    return any(part.type in media_types for part in parts)
