"""Budgeted/cached Graphiti LLM client tests — all offline, fake HTTP only."""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

import httpx
from graphiti_core.prompts.models import Message
from pydantic import BaseModel

from doppel_eval.graphiti_provider import (
    BudgetedCachedGraphitiLLMClient,
    GraphitiProviderBudget,
    GraphitiProviderHardFailure,
)


class _MemoryModel(BaseModel):
    memories: list[str] = []


class _FakeHandler:
    """Records every request; returns a canned completion unless told not to."""

    def __init__(
        self,
        *,
        content: str | None = "{\"memories\": [\"ok\"]}",
        status: int = 200,
        usage: dict | None = None,
    ) -> None:
        self.calls: list[dict] = []
        self._content = content
        self._status = status
        self._usage = usage or {
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "total_tokens": 120,
        }

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(
            {
                "url": str(request.url),
                "json": json.loads(request.content.decode("utf-8")),
            }
        )
        if self._status >= 400:
            return httpx.Response(self._status, json={"error": {"message": "boom"}})
        return httpx.Response(
            self._status,
            json={
                "choices": [{"message": {"content": self._content}}],
                "usage": self._usage,
            },
        )


def _make_client(
    handler: _FakeHandler,
    *,
    model: str = "deepseek-v4-flash",
    budget: GraphitiProviderBudget | None = None,
    cache_dir: Path | None = None,
    max_retries: int = 1,
) -> BudgetedCachedGraphitiLLMClient:
    transport = httpx.MockTransport(handler.handler)
    http = httpx.AsyncClient(transport=transport)
    return BudgetedCachedGraphitiLLMClient(
        model=model,
        base_url="https://api.deepseek.com",
        api_key="sk-test-never-persisted",
        http_client=http,
        budget=budget or GraphitiProviderBudget(max_calls=5),
        cache_dir=cache_dir,
        max_retries=max_retries,
    )


def _messages(text: str = "我长期住在上海") -> list[Message]:
    return [
        Message(role="system", content="你是记忆抽取助手"),
        Message(role="user", content=text),
    ]


class FakeHttpTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="graphiti-provider-")

    async def test_json_object_request_and_no_json_schema(self) -> None:
        handler = _FakeHandler()
        client = _make_client(handler)
        await client.generate_response(_messages(), response_model=_MemoryModel)
        request = handler.calls[0]["json"]
        self.assertEqual(request["response_format"], {"type": "json_object"})
        self.assertNotIn("json_schema", request)
        await client.aclose()

    async def test_schema_injected_into_user_prompt(self) -> None:
        handler = _FakeHandler()
        client = _make_client(handler)
        await client.generate_response(_messages(), response_model=_MemoryModel)
        user_content = handler.calls[0]["json"]["messages"][-1]["content"]
        self.assertIn('"memories"', user_content)
        self.assertIn("Respond with a JSON object", user_content)
        await client.aclose()

    async def test_max_tokens_capped(self) -> None:
        handler = _FakeHandler()
        client = _make_client(handler, budget=GraphitiProviderBudget(max_output_tokens_per_call=1024))
        await client.generate_response(_messages(), max_tokens=16384)
        self.assertEqual(handler.calls[0]["json"]["max_tokens"], 1024)
        await client.aclose()

    async def test_temperature_zero_and_thinking_disabled(self) -> None:
        handler = _FakeHandler()
        client = _make_client(handler, cache_dir=None)
        await client.generate_response(_messages())
        payload = handler.calls[0]["json"]
        self.assertEqual(payload["temperature"], 0.0)
        self.assertEqual(payload["thinking"], {"type": "disabled"})
        await client.aclose()

    async def test_cache_hit_skips_http(self) -> None:
        handler = _FakeHandler()
        client = _make_client(handler, cache_dir=Path(self._tmp) / "cache")
        await client.generate_response(_messages())
        await client.generate_response(_messages())
        self.assertEqual(len(handler.calls), 1)
        self.assertEqual(client.ledger.cache_hits, 1)
        self.assertEqual(client.ledger.calls_attempted, 1)
        await client.aclose()

    async def test_schema_change_misses_cache(self) -> None:
        handler = _FakeHandler()
        client = _make_client(handler, cache_dir=Path(self._tmp) / "cache")
        await client.generate_response(_messages(), response_model=_MemoryModel)

        class _Other(BaseModel):
            value: int = 0

        await client.generate_response(_messages(), response_model=_Other)
        self.assertEqual(len(handler.calls), 2)
        await client.aclose()

    async def test_model_change_misses_cache(self) -> None:
        handler = _FakeHandler()
        client = _make_client(handler, cache_dir=Path(self._tmp) / "cache")
        await client.generate_response(_messages())
        other = _make_client(handler, model="other-model", cache_dir=Path(self._tmp) / "cache")
        try:
            await other.generate_response(_messages())
        finally:
            await other.aclose()
        self.assertEqual(len(handler.calls), 2)
        await client.aclose()

    async def test_max_tokens_change_misses_cache(self) -> None:
        handler = _FakeHandler()
        client = _make_client(handler, cache_dir=Path(self._tmp) / "cache")
        await client.generate_response(_messages(), max_tokens=512)

        class _Other(_MemoryModel):
            pass

        await client.generate_response(_messages(), max_tokens=768)
        self.assertEqual(len(handler.calls), 2)
        await client.aclose()

    async def test_api_key_nowhere_in_cache(self) -> None:
        handler = _FakeHandler()
        cache_dir = Path(self._tmp) / "cache"
        client = _make_client(handler, cache_dir=cache_dir)
        await client.generate_response(_messages())
        blob = "".join(
            path.read_text(encoding="utf-8")
            for path in cache_dir.rglob("*.json")
        )
        self.assertNotIn("sk-test-never-persisted", blob)
        self.assertNotIn("Authorization", blob)
        report = json.dumps(client.ledger.report())
        self.assertNotIn("sk-test", report)
        await client.aclose()

    async def test_usage_recorded(self) -> None:
        handler = _FakeHandler(
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        )
        client = _make_client(handler)
        await client.generate_response(_messages())
        self.assertEqual(client.ledger.input_tokens, 10)
        self.assertEqual(client.ledger.output_tokens, 5)
        self.assertEqual(client.ledger.total_tokens, 15)
        await client.aclose()

    async def test_invalid_json_is_hard_failure(self) -> None:
        handler = _FakeHandler(content="not json")
        client = _make_client(handler)
        with self.assertRaises(GraphitiProviderHardFailure):
            await client.generate_response(_messages())
        self.assertEqual(client.ledger.provider_errors, 1)
        await client.aclose()

    async def test_empty_response_is_hard_failure(self) -> None:
        handler = _FakeHandler(content="")
        client = _make_client(handler)
        with self.assertRaises(GraphitiProviderHardFailure):
            await client.generate_response(_messages())
        await client.aclose()

    async def test_http_400_is_hard_failure(self) -> None:
        handler = _FakeHandler(status=400)
        client = _make_client(handler)
        with self.assertRaises(GraphitiProviderHardFailure) as ctx:
            await client.generate_response(_messages())
        self.assertIn("http_400", str(ctx.exception))
        self.assertEqual(client.ledger.calls_attempted, 1)  # 400 is terminal
        await client.aclose()

    async def test_one_retry_counts_twice(self) -> None:
        handler = _FakeHandler()
        original = handler.handler

        attempt = {"count": 0}

        def flaky(request: httpx.Request) -> httpx.Response:
            attempt["count"] += 1
            if attempt["count"] == 1:
                return httpx.Response(503, json={"error": {"message": "busy"}})
            return original(request)

        transport = httpx.MockTransport(flaky)
        client = BudgetedCachedGraphitiLLMClient(
            model="m",
            base_url="https://api.deepseek.com",
            http_client=httpx.AsyncClient(transport=transport),
            budget=GraphitiProviderBudget(max_calls=5),
            cache_dir=None,
            max_retries=1,
        )
        result = await client.generate_response(_messages())
        self.assertEqual(result, {"memories": ["ok"]})
        self.assertEqual(client.ledger.calls_attempted, 2)
        await client.aclose()

    async def test_max_calls_blocks_before_network(self) -> None:
        handler = _FakeHandler()
        client = _make_client(handler, budget=GraphitiProviderBudget(max_calls=1))
        await client.generate_response(_messages())
        with self.assertRaises(GraphitiProviderHardFailure):
            await client.generate_response(_messages())
        self.assertEqual(len(handler.calls), 1)
        await client.aclose()

    async def test_concurrent_requests_never_exceed_max_calls(self) -> None:
        handler = _FakeHandler()
        client = _make_client(handler, budget=GraphitiProviderBudget(max_calls=3))
        tasks = [
            asyncio.create_task(client.generate_response(_messages(f"消息 {i}")))
            for i in range(10)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        hard_failures = sum(1 for r in results if isinstance(r, GraphitiProviderHardFailure))
        self.assertEqual(len(handler.calls), 3)
        self.assertEqual(hard_failures, 7)
        await client.aclose()

    async def test_cache_hit_not_limited_by_provider_calls(self) -> None:
        handler = _FakeHandler()
        cache_dir = Path(self._tmp) / "cache"
        client = _make_client(handler, budget=GraphitiProviderBudget(max_calls=1), cache_dir=cache_dir)
        await client.generate_response(_messages())
        # second call (same request) = cache hit, allowed even with max_calls=1
        result = await client.generate_response(_messages())
        self.assertEqual(result, {"memories": ["ok"]})
        self.assertEqual(len(handler.calls), 1)
        await client.aclose()

    async def test_token_preflight_blocks_before_network(self) -> None:
        handler = _FakeHandler()
        client = _make_client(
            handler,
            budget=GraphitiProviderBudget(max_calls=5, max_input_tokens=10),
        )
        with self.assertRaises(GraphitiProviderHardFailure):
            await client.generate_response(_messages("很长" * 200))
        self.assertEqual(len(handler.calls), 0)
        await client.aclose()

    async def test_cache_invalid_json_via_cache_hit_still_validates(self) -> None:
        # A cached blob that is invalid JSON must be treated as a miss, not crash.
        cache_dir = Path(self._tmp) / "cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        handler = _FakeHandler()
        client = _make_client(handler, cache_dir=cache_dir)
        # Prime a valid cache entry, then corrupt a copy with a different key shape.
        await client.generate_response(_messages())
        (cache_dir / "corrupt.json").write_text("{broken", encoding="utf-8")
        self.assertIsNone(client._read_cache("corrupt"))
        await client.aclose()


if __name__ == "__main__":
    unittest.main()