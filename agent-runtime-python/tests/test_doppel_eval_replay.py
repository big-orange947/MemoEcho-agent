"""Replay runner tests: audit shape with a fake Doppel + real integration."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from doppel_eval.replay import (
    _logical_to_message_id,
    _put_gold_memories,
    _record_evidence_ids,
    replay_dataset,
    replay_scenarios,
)
from doppel_eval.scenarios import build_scene


class _FakeScope:
    def __init__(self, **kwargs) -> None:
        self.user_id = kwargs.get("user_id", "")
        self.agent_id = kwargs.get("agent_id", "")
        self.platform = kwargs.get("platform", "")
        self.chat_type = kwargs.get("chat_type", "")
        self.chat_id = kwargs.get("chat_id", "")
        self.scope_key = f"{self.user_id}:{self.agent_id}:{self.chat_id}"

    def user_scope(self):
        return _FakeScope(user_id=self.user_id, agent_id=self.agent_id)


class _FakeMessage:
    def __init__(self, **kwargs) -> None:
        self.message_id = kwargs.get("message_id", "")
        self.actor = kwargs.get("actor", "")


class _FakeResult:
    def __init__(self, status: str) -> None:
        self.status = status


class _FakeRecall:
    def __init__(self, message_id: str) -> None:
        self.message_id = message_id


class _FakeClient:
    def __init__(self, *args, **kwargs) -> None:
        self.seen: set[str] = set()
        self.recall_map: dict[str, list[str]] = {}

    async def ingest(self, scope, message) -> _FakeResult:
        if message.message_id in self.seen:
            return _FakeResult("duplicate")
        self.seen.add(message.message_id)
        return _FakeResult("created")

    async def recall(self, query, scopes, *, limit=10) -> list[_FakeRecall]:
        expected = self.recall_map.get(query, [])
        return [_FakeRecall(message_id) for message_id in expected[:limit]]

    async def close(self) -> None:
        return None


class _FakeDoppel:
    MemoryScope = _FakeScope
    ChatMessage = _FakeMessage
    DoppelClient = _FakeClient


class ReplayRunnerTest(unittest.IsolatedAsyncioTestCase):
    async def test_real_extractor_evidence_objects_are_read(self) -> None:
        record = type(
            "Record",
            (),
            {
                "metadata": {
                    "evidence": [
                        {"evidence_id": "message-1"},
                        {"evidence_id": "message-2"},
                    ]
                }
            },
        )()

        self.assertEqual(
            _record_evidence_ids(record), {"message-1", "message-2"}
        )

    async def test_logical_evidence_ids_accept_short_and_qualified_forms(self) -> None:
        scene = build_scene("temporary-trip")
        mapping = _logical_to_message_id(scene)

        self.assertEqual(mapping["m1"], mapping["temporary-trip:m1"])
        self.assertEqual(mapping["m3"], mapping["temporary-trip:m3"])

    async def test_gold_current_snapshot_uses_expected_not_forbidden_evidence(
        self,
    ) -> None:
        class RecordingClient:
            def __init__(self) -> None:
                self.record = None

            async def put(self, record):
                self.record = record
                return _FakeResult("created")

        class Record:
            def __init__(self, **kwargs) -> None:
                self.__dict__.update(kwargs)

        class GoldDoppel:
            MemoryRecord = Record
            MemoryKind = type("MemoryKind", (), {"FACT": "fact"})
            MemoryState = type("MemoryState", (), {"CONFIRMED": "confirmed"})
            FactAuthority = type(
                "FactAuthority",
                (),
                {"HUMAN_SELF": "human_self", "PEER_STATEMENT": "peer_statement"},
            )

        scene = build_scene("temporal-lifecycle")
        scope = _FakeScope(user_id="owner", agent_id="agent")
        logical = _logical_to_message_id(scene)
        client = RecordingClient()

        await _put_gold_memories(GoldDoppel(), client, scene, [scope], logical)

        self.assertEqual(
            client.record.metadata["evidence_ids"], [logical["m5"]]
        )
        self.assertNotIn(logical["m2"], client.record.metadata["evidence_ids"])

    async def test_replay_scenarios_produces_audit(self) -> None:
        report = await replay_scenarios(_FakeDoppel())
        self.assertEqual(report["runner"], "doppel.replay.v1")
        self.assertGreaterEqual(report["summary"]["scenario_count"], 10)
        self.assertGreater(report["summary"]["total_ingested"], 0)
        # every scene has queries and per-query latency recorded
        for scene in report["scenarios"]:
            self.assertIn("case_id", scene)
            for query in scene["queries"]:
                self.assertIn("latency_ms", query)
                self.assertIn("recalled_ids", query)

    async def test_replay_twice_counts_duplicates(self) -> None:
        report = await replay_scenarios(_FakeDoppel(), replay_twice=True)
        # every event ingested a second time must be reported as duplicate
        self.assertGreater(report["summary"]["duplicate_events_total"], 0)
        self.assertEqual(
            report["summary"]["duplicate_events_total"],
            report["summary"]["total_ingested"],
        )

    async def test_replay_dataset_reports_throughput(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            lines = []
            for seq in range(1, 6):
                lines.append(
                    {
                        "eventId": f"syn:test:event:{seq}",
                        "platform": "qq",
                        "scene": "private",
                        "eventType": "message",
                        "chatType": "private",
                        "chatId": "contact-101",
                        "selfId": "10001",
                        "sender": {
                            "id": "contact-101",
                            "name": "联系人",
                            "role": "peer",
                        },
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
                    }
                )
            path.write_text(
                "\n".join(
                    __import__("json").dumps(line, ensure_ascii=False) for line in lines
                ),
                encoding="utf-8",
            )
            report = await replay_dataset(_FakeDoppel(), path, replay_twice=True)
            self.assertEqual(report["event_count"], 5)
            self.assertEqual(report["ingested"], 5)
            self.assertEqual(report["duplicates"], 5)
            self.assertIn("throughput_events_per_sec", report)

    async def test_replay_dataset_does_not_count_failed_as_ingested(self) -> None:
        class FailingClient(_FakeClient):
            async def ingest(self, scope, message) -> _FakeResult:
                return _FakeResult("failed")

        class FailingDoppel(_FakeDoppel):
            DoppelClient = FailingClient

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            path.write_text(
                __import__("json").dumps(
                    {
                        "eventId": "syn:failed:1",
                        "platform": "qq",
                        "eventType": "message",
                        "chatType": "private",
                        "chatId": "contact-101",
                        "selfId": "10001",
                        "sender": {
                            "id": "contact-101",
                            "name": "联系人",
                            "role": "peer",
                        },
                        "text": "失败消息",
                        "timestamp": "2026-08-28T12:00:00+08:00",
                        "actorType": "CONTACT",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            report = await replay_dataset(FailingDoppel(), path)
            self.assertEqual(report["ingested"], 0)
            self.assertEqual(report["status_counts"]["failed"], 1)
            self.assertFalse(report["gate"]["ok"])


class ReplayCliGateTest(unittest.TestCase):
    def test_scenario_replay_is_strict_by_default(self) -> None:
        report = {
            "summary": {"passed_scenarios": 3, "scenario_count": 11},
            "gate": {"ok": True, "strict_passed": False},
        }
        from doppel_eval import __main__ as cli

        with (
            patch.object(cli, "_load_doppel", return_value=object()),
            patch.object(cli, "replay_scenarios", new=AsyncMock(return_value=report)),
        ):
            self.assertEqual(cli.main(["replay", "--scenarios"]), 1)
            self.assertEqual(
                cli.main(["replay", "--scenarios", "--allow-quality-failures"]),
                0,
            )

    def test_e2e_cli_passes_hard_budget_and_quality_exit_policy(self) -> None:
        report = {
            "summary": {"passed_scenarios": 1, "completed_scenarios": 2},
            "gate": {"ok": True, "strict_passed": False},
        }
        from doppel_eval import __main__ as cli

        with (
            patch.object(cli, "_load_doppel", return_value=object()),
            patch.object(cli, "doppel_model", return_value="test-model"),
            patch.object(cli, "run_e2e", new=AsyncMock(return_value=report)) as run,
        ):
            result = cli.main(
                [
                    "e2e",
                    "--cases",
                    "noise-only,temporary-trip",
                    "--max-calls",
                    "2",
                    "--allow-quality-failures",
                ]
            )

        self.assertEqual(result, 0)
        kwargs = run.await_args.kwargs
        self.assertEqual(kwargs["case_ids"], ["noise-only", "temporary-trip"])
        self.assertEqual(kwargs["budget"].max_calls, 2)


class ReplayRealDoppelTest(unittest.IsolatedAsyncioTestCase):
    """Real integration: runs only when DOPPEL_IMPORT_PATH resolves."""

    def _dm(self):
        path = os.environ.get("DOPPEL_IMPORT_PATH", "")
        if not path:
            self.skipTest("DOPPEL_IMPORT_PATH not set")
        from doppel_eval.replay import _load_doppel

        dm = _load_doppel()
        if dm is None:
            self.skipTest("doppel_memory not importable")
        return dm

    async def test_real_doppel_ingests_and_recalls_one_scene(self) -> None:
        dm = self._dm()
        # replay only a small subset via the dataset path using generated events
        from doppel_eval.generators import Tier, TierConfig, generate_dataset

        dataset = generate_dataset(TierConfig(tier=Tier.ADAPTER, seed=5))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "adapter.jsonl"
            dataset.write(path)
            report = await replay_dataset(dm, path)
            self.assertEqual(report["ingested"], len(dataset.events))
            self.assertGreater(report["throughput_events_per_sec"], 0)


if __name__ == "__main__":
    unittest.main()
