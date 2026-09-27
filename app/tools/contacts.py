# =============================================================================
# tools/contacts.py - 联系人解析工具
# -----------------------------------------------------------------------------
# 让 LLM 把"名称/称呼"(如"小号"、"km")解析成真实的会话目标。
# v1 的教训: 称呼是"记忆+语境"问题,不是配置表硬编码问题。
#
# 名称来源有两处,按优先级:
#   1. **别名配置**(contact_aliases): 用户手工配的,最权威("小号" → 某个号);
#   2. **QQ 通讯录**(NapCat 实时返回的备注名/昵称): 覆盖绝大多数日常称呼,
#      不用先配才能用 —— 这是"直接说一句话就能办事"的前提。
# 两处都没有时**不猜**: 返回候选清单或"查不到",让模型回来问号主。
# =============================================================================

from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import tool

from ..services import configs as configs_service


async def _roster() -> tuple[bool, str, list[dict[str, Any]], list[dict[str, Any]]]:
    """读一次 QQ 通讯录,返回 (ok, error, friends, groups)。

    刻意不抛异常: 工具失败要变成**模型能读懂的一句话**,而不是中断整轮执行。
    """
    from ..agent.runtime import get_bridge
    from ..config import get_settings

    bridge = get_bridge()
    if bridge is None or not hasattr(bridge, "get_contacts"):
        return False, "QQ 桥未初始化(服务配置问题)", [], []

    payload = await bridge.get_contacts()
    if not payload.get("ok"):
        return False, str(payload.get("error") or "NapCat 未连接"), [], []

    bot_qq = str(get_settings().bot_qq or "")
    friends = [
        item
        for item in (payload.get("friends") or [])
        if isinstance(item, dict)
        and str(item.get("user_id") or "")
        and str(item.get("user_id")) != bot_qq  # 机器人自己不是可联系对象
    ]
    groups = [
        item for item in (payload.get("groups") or []) if isinstance(item, dict) and str(item.get("group_id") or "")
    ]
    return True, "", friends, groups


def _names_of(item: dict[str, Any], *, group: bool) -> list[str]:
    """取出这个人/群所有可用来称呼它的名字(备注优先)。"""
    if group:
        names = [item.get("group_name"), item.get("name")]
    else:
        names = [item.get("remark"), item.get("nickname"), item.get("nick")]
    return [str(name).strip() for name in names if str(name or "").strip()]


def _target_of(item: dict[str, Any], *, group: bool, fallback_title: str) -> dict[str, str]:
    names = _names_of(item, group=group)
    return {
        "platform": "qq",
        "chat_type": "group" if group else "private",
        "external_id": str(item.get("group_id") if group else item.get("user_id") or ""),
        "title": names[0] if names else fallback_title,
    }


def _match_in_roster(
    name: str, friends: list[dict[str, Any]], groups: list[dict[str, Any]]
) -> list[dict[str, str]]:
    """按名字在通讯录里找;精确命中优先,其次包含匹配。找不到返回空列表。

    为什么要分两级: "km" 既可能是完整备注,也可能是"km老师"的一部分 ——
    前者必须唯一命中,后者只作为候选(宁可让模型看一眼,也不猜错人)。
    """
    wanted = name.strip().casefold()
    if not wanted:
        return []

    exact: list[dict[str, str]] = []
    partial: list[dict[str, str]] = []
    for item, is_group in [(f, False) for f in friends] + [(g, True) for g in groups]:
        names = [n.casefold() for n in _names_of(item, group=is_group)]
        target = _target_of(item, group=is_group, fallback_title=name)
        if wanted in names:
            exact.append(target)
        elif any(wanted in n or n in wanted for n in names):
            partial.append(target)
    return exact or partial


@tool
async def resolve_contact(name: str) -> str:
    """把称呼/名称解析成 QQ 会话目标(需要给谁发消息时先调它)。

    name: 对方在对话里被提到的称呼,例如"小号"、"km"、"计科三班"。
    返回: 命中时是 JSON(platform/chat_type/external_id/title),可直接拿去
          send_qq_message;名字不唯一时返回候选清单(用清单里的号码发,别猜);
          查不到或 QQ 未连接时返回原因与下一步建议。
    """
    # 1. 别名配置(用户手配的,最权威)
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

    # 2. QQ 通讯录(实时备注名/昵称)—— 大多数日常称呼在这一步就能命中
    ok, error, friends, groups = await _roster()
    if not ok:
        return (
            f"查不到「{name}」: {error}(读不到 QQ 通讯录)。"
            "请告诉号主先启动 NapCat 并登录 QQ;或让他直接给出 QQ 号。"
        )

    matches = _match_in_roster(name, friends, groups)
    if len(matches) == 1:
        return json.dumps(matches[0], ensure_ascii=False)
    if len(matches) > 1:
        lines = "、".join(f"{m['title']}({m['external_id']})" for m in matches[:8])
        return (
            f"「{name}」在通讯录里有多个可能: {lines}。"
            "请判断最可能是谁,用它的号码调用 send_qq_message;"
            "实在分不清就问号主,不要随便挑一个。"
        )

    known = sorted({n for f in friends for n in _names_of(f, group=False)})[:20]
    known_groups = sorted({n for g in groups for n in _names_of(g, group=True)})[:10]
    if not known and not known_groups:
        return (
            f"查不到「{name}」,而且 QQ 通讯录是空的(没有好友/群)。"
            "请把这一情况告诉号主 —— 可能还没登录、或好友列表没同步。"
        )
    return (
        f"查不到「{name}」。好友里有: {'、'.join(known)};"
        f"群里有: {'、'.join(known_groups)}。"
        "请先确认是不是这些之一(不要凭印象编号码);确实没有就直说并问号主。"
    )


def _format_roster(friends: list[dict[str, Any]], groups: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for item in friends:
        names = _names_of(item, group=False)
        label = names[0] if names else "(无备注)"
        if len(names) > 1 and names[1] != names[0]:
            label += f"({names[1]})"
        lines.append(f"[好友] {label} qq={item.get('user_id')}")
    for item in groups:
        names = _names_of(item, group=True)
        label = names[0] if names else "(无名)"
        lines.append(f"[群] {label} group={item.get('group_id')}")
    return "\n".join(lines)


@tool
async def list_contacts() -> str:
    """列出 QQ 好友与群(备注名/昵称 + 号码)。

    什么时候用: 想知道"我的 QQ 里都有谁"、或 resolve_contact 没命中时对照着看。
    返回: 一行一个的清单(私聊 [好友]/群聊 [群]);NapCat 未连接时返回提示文本。
    """
    ok, error, friends, groups = await _roster()
    if not ok:
        return f"无法读取联系人: {error}。请提示号主先启动 NapCat 并登录 QQ。"

    body = _format_roster(friends, groups)
    if not body:
        return "联系人清单为空(QQ 里没有好友/群,或 NapCat 未同步)。"
    return "可用联系人:\n" + body


def create_contact_tools() -> list[Any]:
    """返回联系人工具列表。"""
    return [resolve_contact, list_contacts]
