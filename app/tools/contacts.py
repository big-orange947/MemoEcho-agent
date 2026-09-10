# =============================================================================
# tools/contacts.py - 联系人解析工具
# -----------------------------------------------------------------------------
# 让 LLM 把"名称/称呼"(如"小号"、"km")解析成真实的会话目标。
# v1 的教训: 称呼是"记忆+语境"问题,不是配置表硬编码问题。
# 所以这里先查"别名配置",查不到就返回候选列表让 LLM 自己判断,
# 而不是生硬地把账号名当称呼。
# =============================================================================

from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import tool

from ..services import configs as configs_service


@tool
def resolve_contact(name: str) -> str:
    """根据称呼/名称解析联系人,返回可用的会话定位信息。

    name: 对方在对话中被提到的称呼(如"小号"、"km")
    返回: JSON 字符串,包含 platform/chat_type/external_id/title;
          未配置时返回候选列表。
    """
    # 1. 从配置读别名表(JSON: {"小号": {"chat_type":"private","external_id":"2597164807"}})
    raw = configs_service.get_config("contact_aliases", "{}")
    try:
        aliases: dict[str, Any] = json.loads(raw)
    except json.JSONDecodeError:
        aliases = {}

    if name in aliases:
        info = aliases[name]
        return json.dumps(
            {
                "platform": "qq",
                "chat_type": info.get("chat_type", "private"),
                "external_id": info.get("external_id", ""),
                "title": info.get("title", name),
            },
            ensure_ascii=False,
        )

    # 2. 未配置: 返回已知别名清单,让 LLM 判断是否近似匹配
    known = list(aliases.keys())
    if known:
        return f"未找到称呼「{name}」。已知称呼: {known}。请确认是否使用其中之一,或要求对方提供 QQ 号。"
    return f"未找到称呼「{name}」,且没有配置任何联系人别名。请直接询问对方的 QQ 号。"


def create_contact_tools() -> list[Any]:
    """返回联系人工具列表。"""
    return [resolve_contact]
