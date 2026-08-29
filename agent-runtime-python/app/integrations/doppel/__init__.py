"""Doppel shadow evaluation integration (off by default, never replies)."""

from app.integrations.doppel.config import (
    doppel_api_key,
    doppel_db_path,
    doppel_import_path,
    doppel_max_completion_tokens,
    doppel_max_tokens_parameter,
    doppel_model,
    doppel_openai_base_url,
    doppel_schema_mode,
    doppel_thinking,
    shadow_db_path,
    shadow_enabled,
    shadow_extract_enabled,
)
from app.integrations.doppel.shadow_store import ShadowStore
from app.integrations.doppel.shadow_worker import DoppelShadowWorker

__all__ = [
    "DoppelShadowWorker",
    "ShadowStore",
    "doppel_db_path",
    "doppel_import_path",
    "doppel_max_completion_tokens",
    "doppel_max_tokens_parameter",
    "doppel_model",
    "doppel_openai_base_url",
    "doppel_schema_mode",
    "doppel_thinking",
    "doppel_api_key",
    "shadow_db_path",
    "shadow_enabled",
    "shadow_extract_enabled",
]


def build_shadow_worker() -> DoppelShadowWorker | None:
    """Build a worker only when explicitly enabled (env var); None otherwise."""
    if not shadow_enabled():
        return None
    store = ShadowStore(shadow_db_path())
    return DoppelShadowWorker(
        store,
        enabled=True,
        doppel_import_path=doppel_import_path(),
        extract_enabled=shadow_extract_enabled(),
        model=doppel_model(),
        base_url=doppel_openai_base_url(),
        api_key=doppel_api_key(),
        schema_mode=doppel_schema_mode(),
        max_completion_tokens=doppel_max_completion_tokens(),
        max_tokens_parameter=doppel_max_tokens_parameter(),
        thinking=doppel_thinking(),
    )
