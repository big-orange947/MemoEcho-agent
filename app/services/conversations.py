# =============================================================================
# services/conversations.py - 会话与消息服务
# -----------------------------------------------------------------------------
# 职责: conversations / messages 两张表的全部读写操作。
# 被以下模块使用:
#   - agent/nodes/ingest.py    写入入站消息
#   - agent/nodes/retrieve.py  读取历史供 LLM 决策
#   - agent/nodes/finalize.py  写入出站回复
#   - api/routes.py            桌面端会话列表/消息读取
# =============================================================================

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from ..db import get_connection


def _now() -> str:
    """统一时间格式: UTC ISO 字符串。"""
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# 会话
# ---------------------------------------------------------------------------
def ensure_conversation(platform: str, chat_type: str, external_id: str) -> str:
    """按 (platform, chat_type, external_id) 查找会话,不存在则创建。

    这是全系统定位会话的唯一入口 —— QQ 私聊、群聊、桌面线程都走这里。
    返回会话内部 ID(conversation_id),同时作为 LangGraph 的 thread_id。
    """
    conn = get_connection()
    row = conn.execute(
        "SELECT id FROM conversations WHERE platform=? AND chat_type=? AND external_id=?",
        (platform, chat_type, external_id),
    ).fetchone()
    if row is not None:
        return row["id"]

    conversation_id = uuid.uuid4().hex
    now = _now()
    conn.execute(
        "INSERT INTO conversations (id, platform, chat_type, external_id, title, persona, model_name, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, '', '', '', ?, ?)",
        (conversation_id, platform, chat_type, external_id, now, now),
    )
    conn.commit()
    return conversation_id


def ensure_conversation_by_id(conversation_id: str, platform: str = "desktop", chat_type: str = "thread") -> None:
    """确保指定 ID 的会话行存在(桌面端线程场景)。

    桌面端消息直接携带会话 ID(前端创建的线程 ID),但该行可能还没落库;
    这里保底创建一行,否则 messages 的外键约束会拒绝写入。

    兼容脏数据: 若"同三元组但 ID 不同"的旧行存在(例如早期版本用随机 uuid
    建过会话),则把旧行及它的消息迁移到目标 ID,避免 UNIQUE 冲突。
    """
    conn = get_connection()
    if conn.execute("SELECT 1 FROM conversations WHERE id=?", (conversation_id,)).fetchone():
        return  # 目标 ID 已存在,无需处理

    # 同三元组旧行: 迁移 ID(消息/目标一并改挂),保证会话收敛到前端使用的 ID。
    old = conn.execute(
        "SELECT id FROM conversations WHERE platform=? AND chat_type=? AND external_id=?",
        (platform, chat_type, conversation_id),
    ).fetchone()
    if old is not None and old["id"] != conversation_id:
        # 迁移顺序: 先改父表 id,再改子表引用。由于改父表瞬间子表外键会
        # 指向不存在的父行,必须临时关闭外键检查(事务内有效)。
        conn.execute("PRAGMA foreign_keys=OFF")
        try:
            conn.execute("UPDATE conversations SET id=? WHERE id=?", (conversation_id, old["id"]))
            conn.execute("UPDATE messages SET conversation_id=? WHERE conversation_id=?", (conversation_id, old["id"]))
            conn.execute("UPDATE goals SET conversation_id=? WHERE conversation_id=?", (conversation_id, old["id"]))
            conn.commit()
        finally:
            conn.execute("PRAGMA foreign_keys=ON")
        return

    now = _now()
    conn.execute(
        "INSERT INTO conversations (id, platform, chat_type, external_id, title, persona, model_name, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, '', '', '', ?, ?)",
        (conversation_id, platform, chat_type, conversation_id, now, now),
    )
    conn.commit()


def get_conversation(conversation_id: str) -> dict[str, Any] | None:
    """按 ID 读取会话信息(人设/标题等)。"""
    row = get_connection().execute(
        "SELECT * FROM conversations WHERE id=?", (conversation_id,)
    ).fetchone()
    return dict(row) if row is not None else None


def find_conversation(platform: str, chat_type: str, external_id: str) -> dict[str, Any] | None:
    """按平台三元组查找会话(不创建)。"""
    row = get_connection().execute(
        "SELECT * FROM conversations WHERE platform=? AND chat_type=? AND external_id=?",
        (platform, chat_type, external_id),
    ).fetchone()
    return dict(row) if row is not None else None


def list_conversations(limit: int = 100) -> list[dict[str, Any]]:
    """列出最近活跃的会话(桌面端左侧列表用)。"""
    rows = get_connection().execute(
        "SELECT * FROM conversations ORDER BY updated_at DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


def touch_conversation(conversation_id: str) -> None:
    """更新会话的 updated_at(有新消息时调用,用于列表排序)。"""
    conn = get_connection()
    conn.execute(
        "UPDATE conversations SET updated_at=? WHERE id=?",
        (_now(), conversation_id),
    )
    conn.commit()  # 必须提交,否则事务一直持有写锁,其他线程写库会 locked


def update_profile(conversation_id: str, **changes: Any) -> dict[str, Any]:
    """更新会话档案字段(title / persona / model_name)。

    返回 {字段: [旧值, 新值]},便于调用方回报"改了什么"。
    注意: persona 同时承担"值守注意事项"的载体(见 services/policy.py 注释),
    上游主 agent 说"注意事项是…"时写的就是这里。
    """
    allowed = {"title", "persona", "model_name"}
    unknown = set(changes) - allowed
    if unknown:
        raise ValueError(f"不支持的会话字段: {sorted(unknown)}")

    conn = get_connection()
    row = conn.execute("SELECT * FROM conversations WHERE id=?", (conversation_id,)).fetchone()
    if row is None:
        raise ValueError(f"会话不存在: {conversation_id}")
    current = dict(row)

    changed: dict[str, list[Any]] = {}
    for field, value in changes.items():
        if value is None:
            continue
        new_value = str(value)
        if current.get(field) == new_value:
            continue
        changed[field] = [current.get(field), new_value]
        conn.execute(
            f"UPDATE conversations SET {field}=?, updated_at=? WHERE id=?",
            (new_value, _now(), conversation_id),
        )
    if changed:
        conn.commit()
    return changed


# ---------------------------------------------------------------------------
# 消息
# ---------------------------------------------------------------------------
def add_message(conversation_id: str, message: dict[str, Any]) -> str:
    """写入一条消息,返回消息 ID。

    入参 message 支持两种形态(与 nodes 约定一致):
      - 已含 id/created_at(ingest 传入的入站消息)
      - 未含(调用方传 None/空,finalize 传入的出站回复,这里自动生成)
    """
    msg_id = message.get("id") or uuid.uuid4().hex
    created_at = message.get("created_at") or _now()
    conn = get_connection()
    conn.execute(
        "INSERT OR IGNORE INTO messages"
        " (id, conversation_id, role, source, content, raw_json, goal_id, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            msg_id,
            conversation_id,
            message.get("role", "user"),
            message.get("source", "inbound"),
            message.get("content", ""),
            message.get("raw_json", ""),
            message.get("goal_id", ""),
            created_at,
        ),
    )
    conn.commit()
    touch_conversation(conversation_id)
    return msg_id


def message_exists(conversation_id: str, message_id: str) -> bool:
    """判断消息是否已存在(幂等去重)。"""
    row = get_connection().execute(
        "SELECT 1 FROM messages WHERE conversation_id=? AND id=?",
        (conversation_id, message_id),
    ).fetchone()
    return row is not None


def list_messages(conversation_id: str, limit: int = 50) -> list[dict[str, Any]]:
    """读取会话最近 N 条消息(时间正序: 旧→新,LLM 更易理解)。"""
    rows = get_connection().execute(
        "SELECT * FROM messages WHERE conversation_id=? ORDER BY created_at DESC LIMIT ?",
        (conversation_id, limit),
    ).fetchall()
    return [dict(r) for r in reversed(rows)]
