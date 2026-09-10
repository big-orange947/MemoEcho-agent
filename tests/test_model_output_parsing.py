# -*- coding: utf-8 -*-
"""模型输出解析的回归测试(不联网)。

背景: 两条依赖模型的路径,失效方式都是**静默**的 ——
  · 攒批记忆: 解析失败 → 当成"没值得记的" → 水位线推进 → 那批消息永远不再总结;
    而逐条写记忆已被移除,攒批是长期记忆的唯一入口 —— 这条链断了没人会发现;
  · 上报复核: 解析失败 → 退化为纯规则(有兜底,但会失去重要性判断)。

所以解析器必须守住两件事:
  1. 能容错(模型爱加代码块、爱在前面写句话);
  2. **能自曝**: "有输出但解析不出"必须与"模型明确说没有"区分开,前者要能被发现。

真模型上的表现由 scripts/smoke_llm_paths.py 验证;本文件守住解析逻辑。
"""
from __future__ import annotations

import pytest

from app import batches
from app.reports import REVIEW_SYSTEM_PROMPT, build_default_reviewer, parse_review_verdicts


# ---------------------------------------------------------------------------
# 攒批总结的解析
# ---------------------------------------------------------------------------
class TestParseNotes:
    @pytest.fixture(autouse=True)
    def _clean(self):
        batches.reset_summary_stats()
        yield
        batches.reset_summary_stats()

    def test_plain_json_array(self):
        text = '[{"content": "对方周三的课调到八点半", "kind": "fact", "actor": "contact", "importance": 0.8}]'
        notes, unparsed = batches.parse_notes_with_diagnostics(text)
        assert not unparsed
        assert len(notes) == 1
        assert notes[0].content == "对方周三的课调到八点半"
        assert notes[0].actor == "contact"
        assert notes[0].importance == pytest.approx(0.8)

    def test_code_fence_and_chatter_are_tolerated(self):
        """模型常见的两种包装: markdown 代码块、前面先写一句解释。"""
        text = '好的，我整理如下：\n```json\n[{"content": "不喝冰的"}]\n```'
        notes, unparsed = batches.parse_notes_with_diagnostics(text)
        assert not unparsed, "带包装的合法输出不该被判定为可疑"
        assert [n.content for n in notes] == ["不喝冰的"]

    def test_explicit_empty_is_not_suspicious(self):
        """模型明确返回 [] = 这批没价值(正常业务结论),不能算故障。"""
        notes, unparsed = batches.parse_notes_with_diagnostics("[]")
        assert notes == []
        assert unparsed is False

    def test_non_json_output_is_flagged(self):
        """有输出但解析不出 → 必须标记可疑(否则静默丢批次)。"""
        notes, unparsed = batches.parse_notes_with_diagnostics("这段对话没什么好记的。")
        assert notes == []
        assert unparsed is True

    def test_empty_output_not_flagged(self):
        """空输出不算可疑(模型偶尔返回空,属于可接受的抖动)。"""
        notes, unparsed = batches.parse_notes_with_diagnostics("")
        assert notes == []
        assert unparsed is False

    def test_missing_content_field_skipped(self):
        notes, unparsed = batches.parse_notes_with_diagnostics('[{"kind": "fact"}, {"content": "有效"}]')
        assert [n.content for n in notes] == ["有效"]
        assert not unparsed

    def test_plain_string_items_accepted(self):
        """老提示词/弱模型可能直接返回字符串数组 —— 也要能用。"""
        notes, _ = batches.parse_notes_with_diagnostics('["对方喜欢喝美式"]')
        assert [n.content for n in notes] == ["对方喜欢喝美式"]

    def test_bad_importance_falls_back(self):
        notes, _ = batches.parse_notes_with_diagnostics('[{"content": "x", "importance": "很高"}]')
        assert notes[0].importance == pytest.approx(0.5)

    def test_output_is_capped(self):
        """模型偶尔过度输出,条数要有上限(成本兜底)。"""
        many = "[" + ",".join(f'{{"content": "第{i}条"}}' for i in range(50)) + "]"
        notes, _ = batches.parse_notes_with_diagnostics(many)
        assert len(notes) <= batches.MAX_NOTES_PER_RUN

    def test_note_content_is_truncated(self):
        long_text = "[" + '{"content": "' + "长" * 500 + '"}' + "]"
        notes, _ = batches.parse_notes_with_diagnostics(long_text)
        assert len(notes[0].content) <= batches.MAX_NOTE_CHARS


class TestSummaryHealth:
    @pytest.fixture(autouse=True)
    def _clean(self):
        batches.reset_summary_stats()
        yield
        batches.reset_summary_stats()

    def test_health_snapshot_shape(self):
        """健康度快照要能直接给状态页/排障用。"""
        health = batches.summary_health()
        assert set(health) >= {"calls", "empty", "unparsed", "last_unparsed_sample", "unparsed_ratio"}

    def test_sample_is_recorded_on_parse_failure(self):
        """失败时要留下样本 —— 否则只看到"失败了",不知道模型到底输出了什么。"""
        batches.parse_notes_with_diagnostics("不是 JSON 的胡言乱语")
        assert "胡言乱语" in batches.summary_health()["last_unparsed_sample"]

    @pytest.mark.asyncio
    async def test_unparsed_is_counted_by_summarizer(self, monkeypatch):
        """走生产总结器时,"解析失败"要被计数(这是发现静默故障的唯一途径)。"""
        class FakeResponse:
            content = "我觉得没啥好记的"

        class FakeModel:
            async def ainvoke(self, messages):  # noqa: D102 - 测试替身
                return FakeResponse()

        monkeypatch.setattr(batches, "_fast_model", lambda: FakeModel())

        notes = await batches._llm_summarize({"id": "c"}, [{"actor": "contact", "content": "嗯"}])
        assert notes == []
        health = batches.summary_health()
        assert health["calls"] == 1
        assert health["unparsed"] == 1
        assert health["unparsed_ratio"] == pytest.approx(1.0)

    @pytest.mark.asyncio
    async def test_empty_result_is_counted_separately(self, monkeypatch):
        """模型明确说"没有"与"解析失败"要分开计数 —— 前者正常,后者是故障。"""

        class FakeResponse:
            content = "[]"

        class FakeModel:
            async def ainvoke(self, messages):  # noqa: D102
                return FakeResponse()

        monkeypatch.setattr(batches, "_fast_model", lambda: FakeModel())

        await batches._llm_summarize({"id": "c"}, [{"actor": "contact", "content": "嗯"}])
        health = batches.summary_health()
        assert health["empty"] == 1
        assert health["unparsed"] == 0


# ---------------------------------------------------------------------------
# 上报复核的解析
# ---------------------------------------------------------------------------
class TestParseReviewVerdicts:
    def test_plain_array(self):
        text = '[{"id": "m1", "lane": "urgent", "summary": "对方要走了"}]'
        verdicts = parse_review_verdicts(text)
        assert verdicts[0]["id"] == "m1"

    def test_code_fence(self):
        text = '```json\n[{"id": "m1", "lane": "normal"}]\n```'
        assert parse_review_verdicts(text)[0]["lane"] == "normal"

    def test_non_json_raises(self):
        """必须抛异常而不是返回空 —— 空列表在业务上等于"一条都不报",会漏掉急事。

        抛出去由 flush_candidates 捕获并退化为纯规则(消息照常上报)。
        """
        with pytest.raises(ValueError):
            parse_review_verdicts("我无法判断。")

    def test_non_array_raises(self):
        with pytest.raises(ValueError):
            parse_review_verdicts('{"id": "m1"}')

    def test_junk_items_filtered(self):
        text = '[{"id": "m1", "lane": "urgent"}, "噪声", 42]'
        verdicts = parse_review_verdicts(text)
        assert [v["id"] for v in verdicts] == ["m1"]


class TestDefaultReviewer:
    @pytest.mark.asyncio
    async def test_prompt_includes_candidates_and_returns_verdicts(self):
        """复核器要把候选(文本/说话人/命中规则)都送进提示词,并归还判定。"""
        seen: list[str] = []

        class FakeResponse:
            content = '[{"id": "m1", "lane": "urgent", "summary": "要走了"}]'

        class FakeModel:
            async def ainvoke(self, prompt):  # noqa: D102
                seen.append(str(prompt))
                return FakeResponse()

        reviewer = build_default_reviewer(lambda fast=False: FakeModel())
        verdicts = await reviewer(
            [
                {
                    "id": "m1",
                    "payload": {"text": "我七点就走", "sender_name": "km", "reasons": ["keyword:今晚"]},
                }
            ]
        )

        assert verdicts[0]["lane"] == "urgent"
        prompt = seen[0]
        assert "我七点就走" in prompt and "km" in prompt and "keyword:今晚" in prompt
        # 提示词用的是生产常量(避免"测的是复制品")
        assert REVIEW_SYSTEM_PROMPT.splitlines()[0] in prompt
