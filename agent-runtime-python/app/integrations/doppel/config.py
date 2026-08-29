"""Doppel shadow configuration (all off by default)."""

from __future__ import annotations

import os
from pathlib import Path

_ENABLED = "DOPPEL_SHADOW_ENABLED"
_DB = "DOPPEL_SHADOW_DB"
_DOPPEL_DB = "DOPPEL_DB"
_IMPORT = "DOPPEL_IMPORT_PATH"
_EXTRACT = "DOPPEL_SHADOW_EXTRACT_ENABLED"
_MODEL = "DOPPEL_MODEL"
_BASE_URL = "DOPPEL_OPENAI_BASE_URL"
_API_KEY = "DOPPEL_API_KEY"
_SCHEMA_MODE = "DOPPEL_SCHEMA_MODE"
_MAX_COMPLETION_TOKENS = "DOPPEL_MAX_COMPLETION_TOKENS"
_MAX_TOKENS_PARAMETER = "DOPPEL_MAX_TOKENS_PARAMETER"
_THINKING = "DOPPEL_THINKING"


def shadow_enabled() -> bool:
    return os.environ.get(_ENABLED, "").strip().lower() in {"1", "true", "yes", "on"}


def shadow_db_path() -> str:
    """Shadow inbox/trace SQLite file (isolated from MemoEcho data)."""
    override = os.environ.get(_DB, "").strip()
    if override:
        return override
    runtime_root = Path(__file__).resolve().parents[3]  # agent-runtime-python/
    return str(runtime_root / "data" / "doppel-shadow.sqlite3")


def doppel_db_path() -> str:
    """Doppel client store file; never default to the process working dir."""
    override = os.environ.get(_DOPPEL_DB, "").strip()
    if override:
        return override
    runtime_root = Path(__file__).resolve().parents[3]
    return str(runtime_root / "data" / "doppel-memory.sqlite3")


def doppel_import_path() -> str | None:
    value = os.environ.get(_IMPORT, "").strip()
    return value or None


def shadow_extract_enabled() -> bool:
    """Enable online personal-memory extraction after durable event ingest."""
    return os.environ.get(_EXTRACT, "").strip().lower() in {"1", "true", "yes", "on"}


def doppel_model() -> str:
    return (
        os.environ.get(_MODEL, "").strip()
        or os.environ.get("OPENAI_MODEL", "").strip()
    )


def doppel_openai_base_url() -> str:
    return (
        os.environ.get(_BASE_URL, "").strip()
        or os.environ.get("OPENAI_BASE_URL", "").strip()
        or "https://api.openai.com/v1"
    )


def doppel_api_key() -> str:
    return (
        os.environ.get(_API_KEY, "").strip()
        or os.environ.get("OPENAI_API_KEY", "").strip()
    )


def doppel_schema_mode() -> str:
    return os.environ.get(_SCHEMA_MODE, "json_schema").strip() or "json_schema"


def doppel_max_completion_tokens() -> int | None:
    raw = os.environ.get(_MAX_COMPLETION_TOKENS, "").strip()
    return int(raw) if raw else None


def doppel_max_tokens_parameter() -> str:
    return (
        os.environ.get(_MAX_TOKENS_PARAMETER, "max_completion_tokens").strip()
        or "max_completion_tokens"
    )


def doppel_thinking() -> str | None:
    value = os.environ.get(_THINKING, "").strip().lower()
    return value or None
