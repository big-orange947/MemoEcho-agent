# =============================================================================
# tools/memory.py - 长期记忆工具(首版: SQLite 键值事实)
# -----------------------------------------------------------------------------
# 让 LLM 主动"记住"跨会话有用的事实(如"km 的课是晚上八点")。
# 首版实现最简: 事实存 configs 表(JSON 数组),每次全量读取。
# 后续升级路径: 向量检索(embedding + SQLite-vec)或图谱(Neo4j),
# 接口不变,只换内部实现。
# =============================================================================

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from langchain_core.tools import tool

from ..services import configs as configs_service

_MEMORY_KEY = "long_term_facts"


def _load_facts() -> list[dict[str, Any]]:
    """读取全部长期事实。"""
    raw = configs_service.get_config(_MEMORY_KEY, "[]")
    try:
        facts = json.loads(raw)
        return facts if isinstance(facts, list) else []
    except json.JSONDecodeError:
        return []


@tool
def remember_fact(fact: str, subject: str = "") -> str:
    """记住一条跨会话有用的事实。

    fact:    事实内容,如 "km 的课在晚上八点"
    subject: 事实主体(可选),如 "km"
    返回: 保存结果描述。
    """
    facts = _load_facts()
    now = datetime.now(timezone.utc).isoformat()
    # 简单去重: 相同 subject+fact 不重复存
    for item in facts:
        if item.get("subject") == subject and item.get("fact") == fact:
            return "该事实已存在"
    facts.append({"subject": subject, "fact": fact, "created_at": now})
    configs_service.set_config(_MEMORY_KEY, json.dumps(facts, ensure_ascii=False))
    return f"已记住: {fact}"


@tool
def query_memory(query: str) -> str:
    """检索长期记忆,返回相关事实(关键词匹配)。

    query: 要查的内容关键词
    返回: 匹配的事实列表,无结果时提示。
    """
    facts = _load_facts()
    if not facts:
        return "暂无长期记忆"
    # 首版用关键词包含匹配(简单可靠);后续可换向量相似度
    query_lower = query.lower()
    matched = [
        item.get("fact", "")
        for item in facts
        if query_lower in str(item.get("fact", "")).lower()
        or query_lower in str(item.get("subject", "")).lower()
    ]
    if matched:
        return "；".join(matched)
    return f"未找到与「{query}」相关的记忆"


def create_memory_tools() -> list[Any]:
    """返回记忆类工具列表。"""
    return [remember_fact, query_memory]
