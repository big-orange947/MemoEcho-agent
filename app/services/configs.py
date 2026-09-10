# =============================================================================
# services/configs.py - 键值配置服务
# -----------------------------------------------------------------------------
# 职责: configs 表的读写。存用户设置/模型绑定等简单键值,
# 需要更复杂结构时再升级为专用表。
# =============================================================================

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..db import get_connection


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_config(key: str, default: str = "") -> str:
    """读取配置,不存在时返回默认值。"""
    row = get_connection().execute(
        "SELECT value FROM configs WHERE key=?", (key,)
    ).fetchone()
    return row["value"] if row is not None else default


def set_config(key: str, value: str) -> None:
    """写入/更新配置(upsert)。"""
    conn = get_connection()
    conn.execute(
        "INSERT INTO configs (key, value, updated_at) VALUES (?, ?, ?)"
        " ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (key, value, _now()),
    )
    conn.commit()


def get_all_configs() -> dict[str, str]:
    """读取全部配置(桌面端设置页用)。"""
    rows = get_connection().execute("SELECT key, value FROM configs").fetchall()
    return {r["key"]: r["value"] for r in rows}
