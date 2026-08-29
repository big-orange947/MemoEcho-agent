"""Doppel shadow bridge/worker/store tests (no real QQ, no LLM).

Async tests follow the repository convention:
``unittest.IsolatedAsyncioTestCase`` (no pytest-asyncio dependency).
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.integrations.doppel.bridge import (
    AGENT_ID,
    bridge_payload,
    resolve_actor,
    stable_message_id,
    to_message,
    to_scope,
)
from app.integrations.doppel.config import (
    doppel_api_key,
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
from app.integrations.doppel.shadow_worker import DoppelShadowWorker, _DoppelEngine
from app.schemas.events import UnifiedEvent


def _event(
    *,
    event_id: str = "evt-1",
    chat_type: str = "private",
    chat_id: str = "contact-101",
    text: str = "你好",
    actor_type: str | None = "CONTACT",
    role: str = "peer",
    message_origin: str = "",
    direction: str = "in",
    self_id: str = "10001",
    platform_message_id: str | None = "p1",
    client_message_id: str | None = "c1",
) -> UnifiedEvent:
    return UnifiedEvent.model_validate(
        {
            "eventId": event_id,
            "platform": "qq",
            "scene": "private" if chat_type != "group" else "group",
            "eventType": "message",
            "chatType": chat_type,
            "chatId": chat_id,
            "selfId": self_id,
            "sender": {"id": "contact-101", "name": "联系人", "role": role},
            "text": text,
            "attachments": [],
            "mentions": [],
            "timestamp": "2026-08-28T12:00:00+08:00",
            "rawPayload": {"messageOrigin": message_origin},
            "actorType": actor_type,
            "platformMessageId": platform_message_id,
            "clientMessageId": client_message_id,
            "correlationId": None,
            "sequence": 1,
            "sentAt": "2026-08-28T12:00:00+08:00",
            "receivedAt": "2026-08-28T12:00:00+08:00",
            "importedAt": "2026-08-28T12:00:00+08:00",
            "direction": direction,
            "delegatedTaskId": None,
        }
    )


# ---------- actor mapping ----------


class ActorMappingTest(unittest.TestCase):
    def test_actor_type_is_strongest_signal(self) -> None:
        assert (
            resolve_actor(
                _event(actor_type="AGENT", role="self", message_origin="AGENT_AUTO")
            )
            == "agent"
        )
        assert resolve_actor(_event(actor_type="OWNER", role="self")) == "owner"
        assert resolve_actor(_event(actor_type="CONTACT", role="peer")) == "contact"
        assert resolve_actor(_event(actor_type="SYSTEM", role="system")) == "system"

    def test_auto_reply_from_self_account_is_agent_not_owner(self) -> None:
        # role=self but origin AGENT_AUTO -> agent (never an owner fact).
        event = _event(
            actor_type="AGENT",
            role="self",
            message_origin="AGENT_AUTO",
            direction="out",
        )
        assert resolve_actor(event) == "agent"

    def test_manual_owner_message_is_owner(self) -> None:
        event = _event(
            actor_type="OWNER",
            role="self",
            message_origin="USER_MANUAL",
            direction="out",
        )
        assert resolve_actor(event) == "owner"

    def test_peer_message_is_contact(self) -> None:
        assert (
            resolve_actor(_event(actor_type="CONTACT", role="peer", direction="in"))
            == "contact"
        )


# ---------- scope mapping ----------


class ScopeMappingTest(unittest.TestCase):
    def test_private_scope_shape(self) -> None:
        scope = to_scope(
            _event(chat_type="private", chat_id="contact-101", self_id="10001")
        )
        assert scope["user_id"] == "qq:10001"
        assert scope["agent_id"] == AGENT_ID
        assert scope["chat_type"] == "private"
        assert scope["chat_id"] == "qq:contact-101"
        assert scope["extra_dimensions"]["tenant_id"] == "qq-account:10001"

    def test_group_scope_uses_group_namespace(self) -> None:
        scope = to_scope(_event(chat_type="group", chat_id="987654"))
        assert scope["chat_id"] == "qq-group:987654"

    def test_speaker_never_enters_scope(self) -> None:
        scope = to_scope(
            _event(chat_type="group", chat_id="987654", actor_type="CONTACT")
        )
        assert "qq:contact-101" not in scope["chat_id"]
        assert scope["user_id"] == "qq:10001"


# ---------- message mapping ----------


class MessageMappingTest(unittest.TestCase):
    def test_message_identity_priority_client_over_platform(self) -> None:
        event = _event(client_message_id="c-9", platform_message_id="p-9")
        assert "c-9" in stable_message_id(event)
        event_no_client = _event(client_message_id=None, platform_message_id="p-9")
        assert "p-9" in stable_message_id(event_no_client)

    def test_message_maps_actor_and_provenance(self) -> None:
        message = to_message(_event(actor_type="CONTACT", text="我下个月去北京"))
        assert message["actor"] == "contact"
        assert message["text"] == "我下个月去北京"
        assert message["message_id"].startswith("qq:10001:private:contact-101:")

    def test_bridge_payload_contains_all_parts(self) -> None:
        payload = bridge_payload(_event(text="周日爬山吗"))
        assert {"scope", "message", "actor", "event_id", "message_id"} <= set(payload)
        assert payload["actor"] == "contact"


# ---------- shadow store ----------


class ShadowStoreTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="doppel-shadow-")

    def _store(self) -> ShadowStore:
        return ShadowStore(Path(self._tmp) / "shadow.sqlite3")

    async def test_append_is_idempotent(self) -> None:
        store = self._store()
        await store.open()
        inserted_first = await store.append("evt-1", {"a": 1})
        inserted_second = await store.append("evt-1", {"a": 1})
        assert inserted_first is True
        assert inserted_second is False
        counts = await store.counts()
        assert counts["total"] == 1
        await store.close()

    async def test_claim_complete_and_counts(self) -> None:
        store = self._store()
        await store.open()
        await store.append("evt-2", {"a": 2})
        item = await store.claim_next()
        assert item is not None
        assert item["event_id"] == "evt-2"
        await store.complete("evt-2", succeeded=True)
        counts = await store.counts()
        assert counts["pending"] == 0
        assert counts["succeeded"] == 1
        await store.close()

    async def test_failed_retryable_then_dead_letter(self) -> None:
        store = ShadowStore(Path(self._tmp) / "shadow.sqlite3", max_attempts=2)
        await store.open()
        await store.append("evt-3", {"a": 3})
        # first attempt fails -> failed_retryable
        await store.claim_next()
        await store.complete("evt-3", succeeded=False, error="boom")
        counts = await store.counts()
        assert counts["failed_retryable"] == 1
        # second attempt fails -> dead_letter
        await store.requeue_stale()
        await store.claim_next()
        await store.complete("evt-3", succeeded=False, error="boom again")
        counts = await store.counts()
        assert counts["dead_letter"] == 1
        await store.close()

    async def test_requeue_stale_recovers_processing_rows(self) -> None:
        store = self._store()
        await store.open()
        await store.append("evt-4", {"a": 4})
        await store.claim_next()  # moves to processing
        counts = await store.counts()
        assert counts["processing"] == 1
        recovered = await store.requeue_stale()
        assert recovered >= 1
        counts = await store.counts()
        assert counts["processing"] == 0
        assert counts["pending"] == 1
        await store.close()

    async def test_trace_is_persisted(self) -> None:
        store = self._store()
        await store.open()
        await store.trace("evt-5", "bridge", {"scope": "x"})
        await store.close()


# ---------- shadow worker ----------


class ShadowWorkerTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="doppel-shadow-")

    def _store(self) -> ShadowStore:
        return ShadowStore(Path(self._tmp) / "shadow.sqlite3")

    async def test_schedule_observes_and_is_idempotent(self) -> None:
        store = self._store()
        worker = DoppelShadowWorker(store, enabled=True, consumers=1)
        await worker.open()
        event = _event(text="我住在上海")
        assert worker.schedule(event) is True
        # let the persist task settle
        for _ in range(50):
            counts = await store.counts()
            if counts.get("total", 0) >= 1:
                break
            await asyncio.sleep(0.01)
        counts = await store.counts()
        assert counts["total"] == 1
        # replay of the same event id must not duplicate
        assert worker.schedule(event) is True
        for _ in range(50):
            counts = await store.counts()
            if counts.get("total", 0) >= 1:
                break
            await asyncio.sleep(0.01)
        counts = await store.counts()
        assert counts["total"] == 1
        await worker.shutdown()

    async def test_disabled_schedules_nothing(self) -> None:
        store = self._store()
        worker = DoppelShadowWorker(store, enabled=False)
        await worker.open()
        assert worker.schedule(_event(text="忽略我")) is False
        await asyncio.sleep(0.02)
        counts = await store.counts()
        assert counts["total"] == 0
        await worker.shutdown()

    async def test_dead_letter_event_never_enters_inbox(self) -> None:
        store = self._store()
        worker = DoppelShadowWorker(store, enabled=True, consumers=1)
        await worker.open()
        # missing self_id -> bridge errors -> dead-letter trace, no inbox row
        bad_event = UnifiedEvent.model_validate(
            {
                **{k: v for k, v in _event().model_dump(by_alias=True).items()},
                "selfId": "",
            }
        )
        assert worker.schedule(bad_event) is True
        for _ in range(50):
            counts = await store.counts()
            if counts.get("total", 0) > 0:
                break
            await asyncio.sleep(0.01)
        counts = await store.counts()
        assert counts["total"] == 0
        await worker.shutdown()

    async def test_consumer_drains_persisted_inbox_after_restart(self) -> None:
        """A pending row left by a crashed run is processed after restart."""
        store = self._store()
        await store.open()
        await store.append(
            "evt-restart",
            {"a": 1, "event_id": "evt-restart", "scope": {}, "message": {}},
        )
        await store.close()
        worker = DoppelShadowWorker(store, enabled=True, consumers=1)
        await worker.open()  # requeue_stale runs here
        counts = await store.counts()
        assert counts["pending"] >= 1
        await asyncio.sleep(0.05)
        counts = await store.counts()
        # pipeline will fail fast on empty scope/message but must not stay pending forever
        assert counts["pending"] == 0
        await worker.shutdown()

    async def test_default_two_consumers_do_not_lose_concurrent_appends(self) -> None:
        class FakeEngine:
            async def observe(self, payload) -> str:
                return "created"

            async def close(self) -> None:
                return None

        store = self._store()
        worker = DoppelShadowWorker(store, enabled=True, consumers=2)
        worker._engine = FakeEngine()
        await worker.open()
        for index in range(200):
            assert worker.schedule(
                _event(
                    event_id=f"evt-concurrent-{index}",
                    client_message_id=f"client-concurrent-{index}",
                    platform_message_id=f"platform-concurrent-{index}",
                )
            )
        for _ in range(500):
            counts = await store.counts()
            if counts["succeeded"] == 200:
                break
            await asyncio.sleep(0.01)
        self.assertEqual(counts["total"], 200)
        self.assertEqual(counts["succeeded"], 200)
        self.assertEqual(counts["pending"], 0)
        self.assertEqual(counts["processing"], 0)
        await worker.shutdown()

    async def test_retryable_failure_retries_without_restart(self) -> None:
        class FlakyEngine:
            def __init__(self) -> None:
                self.calls = 0

            async def observe(self, payload) -> str:
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("temporary")
                return "created"

            async def close(self) -> None:
                return None

        store = self._store()
        engine = FlakyEngine()
        worker = DoppelShadowWorker(
            store, enabled=True, consumers=1, retry_delay_seconds=0.001
        )
        worker._engine = engine
        await worker.open()
        worker.schedule(_event(event_id="evt-retry"))
        for _ in range(200):
            counts = await store.counts()
            if counts["succeeded"] == 1:
                break
            await asyncio.sleep(0.01)
        self.assertEqual(engine.calls, 2)
        self.assertEqual(counts["succeeded"], 1)
        self.assertEqual(counts["failed_retryable"], 0)
        await worker.shutdown()

    async def test_missing_doppel_is_not_reported_as_succeeded(self) -> None:
        store = ShadowStore(Path(self._tmp) / "missing.sqlite3", max_attempts=2)
        worker = DoppelShadowWorker(
            store, enabled=True, consumers=1, retry_delay_seconds=0.001
        )
        with patch(
            "app.integrations.doppel.shadow_worker._build_engine", return_value=None
        ):
            await worker.open()
            worker.schedule(_event(event_id="evt-no-engine"))
            for _ in range(200):
                counts = await store.counts()
                if counts["dead_letter"] == 1:
                    break
                await asyncio.sleep(0.01)
            self.assertEqual(counts["succeeded"], 0)
            self.assertEqual(counts["dead_letter"], 1)
            await worker.shutdown()

    async def test_engine_optionally_runs_online_memory_extraction(self) -> None:
        class FakeScope:
            def user_scope(self):
                return "user-scope"

        class FakeModule:
            MemoryScope = type(
                "MemoryScope", (), {"__new__": lambda cls, **kwargs: FakeScope()}
            )
            ChatMessage = type(
                "ChatMessage", (), {"__new__": lambda cls, **kwargs: object()}
            )

        class StatusResult:
            status = "created"

        class ProcessingResult:
            errors = []
            proposals = [object()]
            write_results = [StatusResult()]

        class FakeClient:
            def __init__(self) -> None:
                self.allowed_scopes = None

            async def ingest(self, scope, message):
                return StatusResult()

            async def process(self, scope, message, *, processors, allowed_scopes):
                self.allowed_scopes = allowed_scopes
                self.processors = processors
                return ProcessingResult()

            async def close(self) -> None:
                return None

        client = FakeClient()
        extractor = object()
        engine = _DoppelEngine(FakeModule(), client, extractor=extractor)
        result = await engine.observe({"scope": {}, "message": {}})
        self.assertEqual(result["event_status"], "created")
        self.assertTrue(result["extraction_enabled"])
        self.assertEqual(result["proposal_count"], 1)
        self.assertEqual(result["memory_write_count"], 1)
        self.assertEqual(client.allowed_scopes, ["user-scope"])
        self.assertEqual(client.processors, [extractor])
        await engine.close()


# ---------- config ----------


class AttachmentBridgeTest(unittest.TestCase):
    def _attachment_event(self) -> UnifiedEvent:
        return UnifiedEvent.model_validate(
            {
                "eventId": "evt-att-1",
                "platform": "qq",
                "scene": "private",
                "eventType": "message",
                "chatType": "private",
                "chatId": "contact-101",
                "selfId": "10001",
                "sender": {"id": "contact-101", "name": "联系人", "role": "peer"},
                "text": "图片看下",
                "attachments": [
                    {
                        "fileId": "file-abc",
                        "fileName": "photo.png",
                        "fileType": "image/png",
                        "url": "https://example.invalid/photo.png",
                    }
                ],
                "mentions": [],
                "timestamp": "2026-08-28T12:00:00+08:00",
                "rawPayload": {},
                "actorType": "CONTACT",
                "platformMessageId": "p-att",
                "clientMessageId": "c-att",
                "correlationId": None,
                "sequence": 1,
                "sentAt": "2026-08-28T12:00:00+08:00",
                "receivedAt": "2026-08-28T12:00:00+08:00",
                "importedAt": "2026-08-28T12:00:00+08:00",
                "direction": "in",
                "delegatedTaskId": None,
            }
        )

    def test_attachment_uses_file_type_not_type(self) -> None:
        message = to_message(self._attachment_event())
        self.assertEqual(message["attachments"][0]["type"], "image/png")
        self.assertEqual(message["attachments"][0]["file_id"], "file-abc")
        self.assertEqual(message["message_type"], "image")
        self.assertEqual(message["parts"][0]["type"], "image")
        self.assertEqual(message["parts"][0]["media"]["media_id"], "file-abc")

    def test_message_type_normalizes_message_sent(self) -> None:
        event = self._attachment_event()
        event = UnifiedEvent.model_validate(
            {**event.model_dump(by_alias=True), "eventType": "message_sent"}
        )
        message = to_message(event)
        # message_sent (outbound) normalizes to a content type, not a post_type
        self.assertEqual(message["message_type"], "image")
        self.assertEqual(message["raw"]["platform_event_type"], "message_sent")

    def test_text_only_message_type_is_text(self) -> None:
        message = to_message(_event(text="普通消息"))
        self.assertEqual(message["message_type"], "text")

    def test_parts_map_segments(self) -> None:
        event = UnifiedEvent.model_validate(
            {
                "eventId": "evt-seg",
                "platform": "qq",
                "scene": "private",
                "eventType": "message",
                "chatType": "private",
                "chatId": "contact-101",
                "selfId": "10001",
                "sender": {"id": "contact-101", "name": "联系人", "role": "peer"},
                "text": "你好 @某人 [图片]",
                "attachments": [],
                "mentions": ["20001"],
                "segments": [
                    {"type": "text", "data": {"text": "你好"}},
                    {"type": "at", "data": {"qq": "20001", "name": "某人"}},
                    {
                        "type": "image",
                        "data": {"file": "img-1", "url": "https://x.invalid/1.png"},
                    },
                ],
                "timestamp": "2026-08-28T12:00:00+08:00",
                "rawPayload": {},
                "actorType": "CONTACT",
                "platformMessageId": "p-seg",
                "clientMessageId": "c-seg",
                "correlationId": None,
                "sequence": 1,
                "sentAt": "2026-08-28T12:00:00+08:00",
                "receivedAt": "2026-08-28T12:00:00+08:00",
                "importedAt": "2026-08-28T12:00:00+08:00",
                "direction": "in",
                "delegatedTaskId": None,
            }
        )
        message = to_message(event)
        types = [part["type"] for part in message["parts"]]
        self.assertEqual(types, ["text", "mention", "image"])
        mention = message["parts"][1]
        self.assertEqual(mention["metadata"]["qq"], "20001")
        self.assertEqual(message["message_type"], "image")  # image segment wins

    def test_raw_provenance_is_whitelisted(self) -> None:
        message = to_message(_event(text="审计", direction="in"))
        raw = message["raw"]
        self.assertIn("actor_type", raw)
        self.assertIn("platform_event_type", raw)
        self.assertIn("sequence", raw)
        self.assertIn("adapter", raw)
        self.assertNotIn("rawPayload", raw)


class UnsafeIdentityBridgeTest(unittest.TestCase):
    def test_missing_self_id_is_dead_lettered(self) -> None:
        event = UnifiedEvent.model_validate(
            {
                **{k: v for k, v in _event().model_dump(by_alias=True).items()},
                "selfId": "",
            }
        )
        payload = bridge_payload(event)
        self.assertTrue(payload["errors"])
        self.assertIsNone(payload["scope"])

    def test_unsupported_platform_is_dead_lettered(self) -> None:
        event = UnifiedEvent.model_validate(
            {
                **{k: v for k, v in _event().model_dump(by_alias=True).items()},
                "platform": "telegram",
            }
        )
        payload = bridge_payload(event)
        self.assertTrue(any("platform" in error for error in payload["errors"]))
        self.assertIsNone(payload["scope"])

    def test_valid_event_has_no_errors(self) -> None:
        payload = bridge_payload(_event(text="正常"))
        self.assertEqual(payload["errors"], [])
        self.assertIsNotNone(payload["scope"])


class ConfigTest(unittest.TestCase):
    _ENV_KEYS = (
        "DOPPEL_SHADOW_ENABLED",
        "DOPPEL_SHADOW_DB",
        "DOPPEL_IMPORT_PATH",
        "DOPPEL_SHADOW_EXTRACT_ENABLED",
        "DOPPEL_MODEL",
        "DOPPEL_OPENAI_BASE_URL",
        "DOPPEL_API_KEY",
        "OPENAI_MODEL",
        "OPENAI_BASE_URL",
        "OPENAI_API_KEY",
        "DOPPEL_SCHEMA_MODE",
        "DOPPEL_MAX_COMPLETION_TOKENS",
        "DOPPEL_MAX_TOKENS_PARAMETER",
        "DOPPEL_THINKING",
    )

    def setUp(self) -> None:
        import os

        self._original_env = {key: os.environ.get(key) for key in self._ENV_KEYS}

    def tearDown(self) -> None:
        import os

        for key, value in self._original_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _clear_env(self) -> None:
        import os

        for key in self._ENV_KEYS:
            os.environ.pop(key, None)

    def test_defaults_are_off(self) -> None:
        self._clear_env()
        assert shadow_enabled() is False
        assert shadow_extract_enabled() is False
        assert doppel_import_path() is None
        assert shadow_db_path().endswith("doppel-shadow.sqlite3")

    def test_env_enables(self) -> None:
        import os

        os.environ["DOPPEL_SHADOW_ENABLED"] = "1"
        os.environ["DOPPEL_SHADOW_DB"] = "C:/tmp/x.sqlite3"
        os.environ["DOPPEL_IMPORT_PATH"] = "D:/project/Doppel"
        try:
            assert shadow_enabled() is True
            assert shadow_db_path() == "C:/tmp/x.sqlite3"
            assert doppel_import_path() == "D:/project/Doppel"
        finally:
            os.environ.pop("DOPPEL_SHADOW_ENABLED", None)
            os.environ.pop("DOPPEL_SHADOW_DB", None)
            os.environ.pop("DOPPEL_IMPORT_PATH", None)

    def test_extract_provider_settings(self) -> None:
        import os

        os.environ["DOPPEL_SHADOW_EXTRACT_ENABLED"] = "true"
        os.environ["DOPPEL_MODEL"] = "test-model"
        os.environ["DOPPEL_OPENAI_BASE_URL"] = "http://localhost:11434/v1"
        self.assertTrue(shadow_extract_enabled())
        self.assertEqual(doppel_model(), "test-model")
        self.assertEqual(doppel_openai_base_url(), "http://localhost:11434/v1")

    def test_provider_settings_fall_back_to_shared_openai_env(self) -> None:
        import os

        self._clear_env()
        os.environ["OPENAI_MODEL"] = "shared-model"
        os.environ["OPENAI_BASE_URL"] = "http://localhost:1234/v1"
        os.environ["OPENAI_API_KEY"] = "shared-secret"

        self.assertEqual(doppel_model(), "shared-model")
        self.assertEqual(doppel_openai_base_url(), "http://localhost:1234/v1")
        self.assertEqual(doppel_api_key(), "shared-secret")

        os.environ["DOPPEL_MODEL"] = "doppel-model"
        os.environ["DOPPEL_OPENAI_BASE_URL"] = "http://localhost:11434/v1"
        os.environ["DOPPEL_API_KEY"] = "doppel-secret"

        self.assertEqual(doppel_model(), "doppel-model")
        self.assertEqual(doppel_openai_base_url(), "http://localhost:11434/v1")
        self.assertEqual(doppel_api_key(), "doppel-secret")

    def test_deepseek_compatible_provider_options(self) -> None:
        import os

        self._clear_env()
        os.environ["DOPPEL_SCHEMA_MODE"] = "json_object"
        os.environ["DOPPEL_MAX_COMPLETION_TOKENS"] = "1024"
        os.environ["DOPPEL_MAX_TOKENS_PARAMETER"] = "max_tokens"
        os.environ["DOPPEL_THINKING"] = "disabled"

        self.assertEqual(doppel_schema_mode(), "json_object")
        self.assertEqual(doppel_max_completion_tokens(), 1024)
        self.assertEqual(doppel_max_tokens_parameter(), "max_tokens")
        self.assertEqual(doppel_thinking(), "disabled")

    def test_never_touches_production_data(self) -> None:
        store = ShadowStore("C:/tmp/only-shadow.sqlite3")
        assert "only-shadow" in store._path
