# -*- coding: utf-8 -*-
"""事件判定测试(app/event_rules.py)。

这一层回答"这条消息值不值得让号主知道、该多急"。测试的重点不是每个词表,
而是**组合规则**与**闸门**:

· 组合: "改"+"明天四点" 才是排期变更;只有"改"可能是"改天再说";
· 兜底: "明天下午三点见"没有动词,但它是实打实的时间安排,不能漏;
· 闸门: 分数高不等于紧急 —— 没有"为什么现在就得看"的理由只能进 normal;
· 抑制: "没事了/不用管"要降分,但不能一票否决(误杀真事代价更大)。

固定"现在"为 2026-09-11(周五)19:00,避免测试在不同时刻给出不同结论。
"""
from __future__ import annotations

from datetime import datetime

import pytest

from app.event_rules import (
    KIND_DEADLINE,
    KIND_MENTION,
    KIND_MONEY,
    KIND_SCHEDULE_CHANGE,
    KIND_TIME_PLAN,
    classify,
    lane_for_score,
)
from app.signals import _tz, extract_all

NOW = datetime(2026, 9, 11, 19, 0, tzinfo=_tz())


def judge(text: str, **configured):
    signals = extract_all(text, now=NOW)
    configured.setdefault("chat_type", "private")
    return classify(signals, configured=configured, now=NOW)


# ---------------------------------------------------------------------------
# 组合规则: 关键词不够,要看组合
# ---------------------------------------------------------------------------
class TestCombination:
    def test_schedule_change_needs_time(self):
        """"改" + 具体时间 = 排期变更(高分);只有"改" = 低分(可能只是"改天")。"""
        with_time = judge("明天组会改到四点")
        without_time = judge("那事改一下吧")
        assert with_time["event"] == KIND_SCHEDULE_CHANGE
        assert without_time["event"] == KIND_SCHEDULE_CHANGE
        assert with_time["score"] > without_time["score"]
        assert with_time["lane"] == "urgent"

    def test_cancellation_counts_as_change(self):
        """"不去了/取消"也是排期变更 —— 它是"事情变了",不是闲聊。"""
        verdict = judge("今晚的球不去了")
        assert verdict["event"] == KIND_SCHEDULE_CHANGE
        assert verdict["lane"] in ("urgent", "normal")

    def test_settled_is_low_priority(self):
        """"不改了"是事件(事情定了),但不紧急。"""
        verdict = judge("不用改了，照旧")
        assert verdict["event"] == "settled"
        assert verdict["lane"] == "digest"

    def test_deadline_with_specific_time(self):
        verdict = judge("奖学金材料周五截止")
        assert verdict["event"] == KIND_DEADLINE
        assert verdict["lane"] == "urgent"

    def test_fuzzy_deadline_is_not_urgent(self):
        """"月底前交"是几周后的事 —— 值得记,但不该现在打扰人。"""
        verdict = judge("月底前把这学期的报告交了")
        assert verdict["event"] == KIND_DEADLINE
        assert verdict["lane"] == "normal", "没有具体时间的期限不该判成紧急"

    def test_time_plan_without_verb(self):
        """没有动词、只有时间安排的句子也必须能报出来。

        这是最容易漏的一类: 词表里一个词都不命中,但它是个实打实的约定。
        """
        verdict = judge("明天下午三点见")
        assert verdict["event"] == KIND_TIME_PLAN
        assert verdict["lane"] == "normal"

    def test_money(self):
        assert judge("借我50块钱")["event"] == KIND_MONEY
        assert judge("今晚把钱转你")["event"] == KIND_MONEY

    def test_mention(self):
        verdict = judge("@3969785168 看下这个", at_bot=True, chat_type="group")
        assert verdict["event"] == KIND_MENTION
        assert verdict["lane"] == "normal"


# ---------------------------------------------------------------------------
# 不报 / 少报: 别把闲聊变成打扰
# ---------------------------------------------------------------------------
class TestNoise:
    @pytest.mark.parametrize(
        "text",
        ["哈哈哈", "改天再聊", "有一点事想问你", "差一点就忘了", "好的", "嗯嗯", "在的"],
    )
    def test_small_talk_not_queued(self, text):
        assert judge(text)["lane"] == ""

    def test_urgency_word_alone_does_not_trigger(self):
        """"别急"里有"急",但它明确是"不用管"的意思 —— 不能只按关键词命中。"""
        assert judge("别急，没事了")["lane"] == ""

    def test_suppress_lowers_score(self):
        """"不用管了"降分但不一票否决: 误杀一条真事的代价更大。"""
        plain = judge("明天组会改到四点，记得来")
        suppressed = judge("明天组会改到四点，但不用管我了")
        assert suppressed["score"] < plain["score"]

    def test_group_chatter_is_downweighted(self):
        """群里没被点名的泛泛消息降权(但白名单/@/关键词命中时不被降)。"""
        noisy = judge("今晚一起打球吗", chat_type="group")
        private = judge("今晚一起打球吗", chat_type="private")
        assert noisy["score"] < private["score"]

    def test_watchlist_sender_always_worth_knowing(self):
        verdict = judge("到了", watchlist=True)
        assert verdict["event"] == "watchlist"
        assert verdict["lane"] == "normal"


# ---------------------------------------------------------------------------
# 闸门: 紧急需要理由
# ---------------------------------------------------------------------------
class TestUrgencyGate:
    def test_far_future_change_is_not_urgent(self):
        """两周后的变更不急 —— 高分也要有"为什么现在就得看"才能进 urgent。"""
        verdict = judge("10月8号的活动改到10月9号")
        assert verdict["event"] == KIND_SCHEDULE_CHANGE
        assert verdict["lane"] == "normal"
        assert "not_urgent:no_time_pressure" in verdict["boost"]

    def test_imminent_change_is_urgent(self):
        assert judge("今晚七点改到八点半")["lane"] == "urgent"

    def test_urgency_word_opens_the_gate(self):
        verdict = judge("明天的组会取消了，挺急的")
        assert verdict["lane"] == "urgent"
        assert any(item.startswith("urgent_word:") for item in verdict["boost"])

    def test_temporal_escalation_happens_later(self):
        """时间临近才升级 —— 静态规则做不到,交给后台按时间推进(见 reports.rescore_pending)。"""
        soon = judge("今天下午三点见")          # 今晚 19:00 看"今天 15:00" = 已过
        assert soon["event"] == KIND_TIME_PLAN


# ---------------------------------------------------------------------------
# 上下文
# ---------------------------------------------------------------------------
class TestContext:
    def test_active_goal_raises_priority(self):
        """同一句话,撞上正在推进的任务时更值得立刻知道。"""
        plain = judge("组会改到四点了")
        in_task = judge("组会改到四点了", active_goal=True)
        assert in_task["score"] > plain["score"]
        assert "active_goal" in in_task["boost"]

    def test_burst_raises_priority(self):
        """对方连发几条没人回 —— 普通的"在吗"也变成值得知道的事。"""
        verdict = judge("在吗", unread=4)
        assert verdict["event"] == "burst"
        assert verdict["lane"] == "normal"

    def test_single_unread_stays_quiet(self):
        assert judge("在吗", unread=1)["lane"] == "digest"

    def test_keywords_stack(self):
        one = judge("改时间的事", keywords=["改时间"])
        two = judge("改时间的事", keywords=["改时间", "改"])
        assert two["score"] >= one["score"]


# ---------------------------------------------------------------------------
# 阈值
# ---------------------------------------------------------------------------
class TestLane:
    @pytest.mark.parametrize(
        "score,lane",
        [(0.95, "urgent"), (0.8, "urgent"), (0.79, "normal"), (0.55, "normal"),
         (0.54, "digest"), (0.4, "digest"), (0.39, ""), (0.0, "")],
    )
    def test_thresholds(self, score, lane):
        assert lane_for_score(score) == lane


class TestEvidence:
    def test_verdict_carries_evidence(self):
        """每个结论都要能回答"凭什么" —— 通知与排障都靠它。"""
        verdict = judge("明天组会改到四点")
        assert verdict["evidence"]["change"]
        assert verdict["evidence"]["affair"]
        assert verdict["time"]["raw"] == "明天四点"

    def test_reasons_are_readable(self):
        verdict = judge("明天组会改到四点")
        assert "event:schedule_change" in verdict["reasons"]
        assert any(item.startswith("time_") for item in verdict["reasons"])

    def test_news_event_kind_is_stable(self):
        """事件类型是给下游用的稳定标识,不是给人看的句子 —— 别随手改。"""
        assert judge("借我50块钱")["event"] == "money"
        assert judge("下周面试")["event"] in ("affair", "time_plan")
