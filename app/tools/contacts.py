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


@tool
async def list_contacts() -> str:
    """列出 QQ 好友与群(昵称/备注名 + 号码),用于把"km"这样的称呼对到 QQ 号。

    什么时候用: 需要给某人/某个群发消息,但不知道对方的 QQ 号时先调它。
    返回: 一行一个的清单(私聊标注 [好友],群聊标注 [群]),含备注名与昵称;
          NapCat 未连接时返回提示文本(此时无法确认联系人,应告知用户)。
    """
    from ..agent.runtime import get_bridge

    bridge = get_bridge()
    if bridge is None or not hasattr(bridge, "get_contacts"):
        return "无法读取联系人: QQ 桥未初始化(服务配置问题)。"

    payload = await bridge.get_contacts()
    if not payload.get("ok"):
        reason = payload.get("error") or "NapCat 未连接"
        return f"无法读取联系人: {reason}。请提示用户先启动 NapCat 并登录 QQ。"

    from ..config import get_settings

    bot_qq = str(get_settings().bot_qq or "")
    lines: list[str] = []
    for item in payload.get("friends") or []:
        if not isinstance(item, dict):
            continue
        user_id = str(item.get("user_id") or "")
        if not user_id or user_id == bot_qq:
            continue  # 机器人自己不是可联系对象
        remark = str(item.get("remark") or "").strip()
        nickname = str(item.get("nickname") or "").strip()
        name = f"{remark}({nickname})" if remark and nickname and remark != nickname else (remark or nickname)
        lines.append(f"[好友] {name or '(无备注)'} qq={user_id}")
    for item in payload.get("groups") or []:
        if not isinstance(item, dict):
            continue
        group_id = str(item.get("group_id") or "")
        if not group_id:
            continue
        name = str(item.get("group_name") or "").strip() or "(无名)"
        lines.append(f"[群] {name} group={group_id}")

    if not lines:
        return "联系人清单为空(QQ 里没有好友/群,或 NapCat 未同步)。"
    return "可用联系人:\n" + "\n".join(lines)


def create_contact_tools() -> list[Any]:
    """返回联系人工具列表。"""
    return [resolve_contact, list_contacts]
