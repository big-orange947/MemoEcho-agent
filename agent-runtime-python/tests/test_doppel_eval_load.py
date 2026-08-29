"""Load runner tests (fake Doppel) + real integration (conditional)."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from doppel_eval.load import run_load


class _FakeScope:
    def __init__(self, **kwargs) -> None:
        self.user_id = kwargs.get("user_id", "")
        self.agent_id = kwargs.get("agent_id", "")
        self.platform = kwargs.get("platform", "")
        self.chat_type = kwargs.get("chat_type", "")
        self.chat_id = kwargs.get("chat_id", "")
        self.scope_key = f"{self.user_id}:{self.agent_id}:{self.chat_id}"


class _FakeRecord:
    def __init__(self, scope, memory_id: str) -> None:
        self.scope = scope
        self.memory_id = memory_id


class _FakePage:
    def __init__(self, records) -> None:
        self.records = records
        self.has_more = False
        self.next_cursor = ""


class _FakeMessage:
    def __init__(self, **kwargs) -> None:
        self.message_id = kwargs.get("message_id", "")


class _FakeResult:
    def __init__(self, status: str) -> None:
        self.status = status


class _FakeStore:
    def __init__(self) -> None:
        self.records: list[_FakeRecord] = []

    async def scan(self, scope, filters=None, limit=50) -> _FakePage:
        own = [r for r in self.records if r.scope.scope_key == scope.scope_key]
        return _FakePage(own[:limit])


class _FakeClient:
    MemoryState = type(
        "MS",
        (),
        {
            "CONFIRMED": "confirmed",
            "CANDIDATE": "candidate",
            "SUPERSEDED": "superseded",
        },
    )
    MemoryFilter = type("MF", (), {"__init__": lambda self, **kw: None})

    def __init__(self, backend="sqlite", **kwargs) -> None:
        self.seen: set[str] = set()
        self.store = _FakeStore()

    async def ingest(self, scope, message) -> _FakeResult:
        if message.message_id in self.seen:
            return _FakeResult("duplicate")
        self.seen.add(message.message_id)
        self.store.records.append(_FakeRecord(scope, message.message_id))
        return _FakeResult("created")

    async def close(self) -> None:
        return None


class _FakeDoppel:
    MemoryScope = _FakeScope
    ChatMessage = _FakeMessage
    DoppelClient = _FakeClient
    MemoryState = _FakeClient.MemoryState
    MemoryFilter = _FakeClient.MemoryFilter


def _write_dataset(path: Path, event_count: int, *, chat_ids: list[str]) -> None:
    lines = []
    for seq in range(1, event_count + 1):
        chat_id = chat_ids[seq % len(chat_ids)]
        lines.append(
            json.dumps(
                {
                    "eventId": f"syn:load:{seq}",
                    "platform": "qq",
                    "scene": "private",
                    "eventType": "message",
                    "chatType": "private",
                    "chatId": chat_id,
                    "selfId": "10001",
                    "sender": {"id": "contact-101", "name": "联系人", "role": "peer"},
                    "text": f"消息 {seq}",
                    "attachments": [],
                    "mentions": [],
                    "timestamp": "2026-08-28T12:00:00+08:00",
                    "rawPayload": {},
                    "actorType": "CONTACT",
                    "platformMessageId": f"p-{seq}",
                    "clientMessageId": f"c-{seq}",
                    "correlationId": None,
                    "sequence": seq,
                    "sentAt": "2026-08-28T12:00:00+08:00",
                    "receivedAt": "2026-08-28T12:00:00+08:00",
                    "importedAt": "2026-08-28T12:00:00+08:00",
                    "direction": "in",
                    "delegatedTaskId": None,
                },
                ensure_ascii=False,
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class LoadRunnerTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="doppel-load-")

    async def test_load_reports_throughput_and_isolation(self) -> None:
        path = Path(self._tmp) / "load.jsonl"
        _write_dataset(path, 200, chat_ids=["contact-1", "contact-2", "contact-3"])
        report = await run_load(_FakeDoppel(), path, isolation_check_scopes=3)
        self.assertEqual(report["write_attempts"], 200)
        self.assertEqual(report["status_counts"]["created"], 200)
        self.assertEqual(report["scope_count"], 3)
        self.assertGreater(report["throughput_events_per_sec"]["first_pass"], 0)
        self.assertTrue(report["isolation"]["passed"])
        self.assertEqual(report["isolation"]["checked_scopes"], 3)
        self.assertTrue(report["gate"]["ok"])

    async def test_load_replay_twice_counts_duplicates(self) -> None:
        path = Path(self._tmp) / "load.jsonl"
        _write_dataset(path, 100, chat_ids=["contact-1"])
        report = await run_load(_FakeDoppel(), path, replay_twice=True)
        self.assertEqual(report["status_counts"]["created"], 100)
        self.assertEqual(report["status_counts"]["duplicate"], 100)
        self.assertEqual(report["source_events"], 100)
        self.assertEqual(report["write_attempts"], 200)
        self.assertEqual(report["pass_attempts"]["first_pass"], 100)
        self.assertEqual(report["pass_attempts"]["replay"], 100)
        self.assertIn("replay", report["throughput_events_per_sec"])
        self.assertTrue(report["gate"]["ok"])

    async def test_load_failed_writes_fail_the_gate(self) -> None:
        path = Path(self._tmp) / "load.jsonl"
        _write_dataset(path, 40, chat_ids=["contact-1"])

        class FailingClient(_FakeClient):
            async def ingest(self, scope, message) -> _FakeResult:
                return _FakeResult("failed")

        class FailingDoppel(_FakeDoppel):
            DoppelClient = FailingClient

        report = await run_load(FailingDoppel(), path)
        self.assertEqual(report["status_counts"]["failed"], 40)
        self.assertFalse(report["gate"]["ok"])
        self.assertEqual(report["gate"]["failed_writes"], 40)

    async def test_load_scope_isolation_detects_violations(self) -> None:
        path = Path(self._tmp) / "load.jsonl"
        _write_dataset(path, 60, chat_ids=["contact-a", "contact-b"])

        class LeakyStore(_FakeStore):
            async def scan(self, scope, filters=None, limit=50) -> _FakePage:
                # leak everything into every scope (worst case)
                return _FakePage(self.records[:limit])

        class LeakyClient(_FakeClient):
            def __init__(self, backend="sqlite", **kwargs) -> None:
                super().__init__()
                self.store = LeakyStore()

        class LeakyDoppel(_FakeDoppel):
            DoppelClient = LeakyClient

        report = await run_load(LeakyDoppel(), path, isolation_check_scopes=2)
        self.assertFalse(report["isolation"]["passed"])
        self.assertGreater(report["isolation"]["violations"], 0)


class LoadRealDoppelTest(unittest.IsolatedAsyncioTestCase):
    def _dm(self):
        path = os.environ.get("DOPPEL_IMPORT_PATH", "")
        if not path:
            self.skipTest("DOPPEL_IMPORT_PATH not set")
        from doppel_eval.replay import _load_doppel

        dm = _load_doppel()
        if dm is None:
            self.skipTest("doppel_memory not importable")
        return dm

    async def test_real_doppel_loads_1k_events(self) -> None:
        dm = self._dm()
        from doppel_eval.generators import Tier, TierConfig, generate_dataset

        dataset = generate_dataset(
            TierConfig(
                tier=Tier.LOAD,
                load_events=1000,
                load_private_scopes=10,
                load_group_scopes=2,
            )
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "load-1k.jsonl"
            dataset.write(path)
            report = await run_load(dm, path, isolation_check_scopes=3)
            self.assertEqual(report["write_attempts"], 1000)
            self.assertEqual(report["status_counts"]["created"], 1000)
            self.assertEqual(report["scope_count"], 12)
            self.assertTrue(report["isolation"]["passed"])
            self.assertTrue(report["gate"]["ok"])


if __name__ == "__main__":
    unittest.main()
