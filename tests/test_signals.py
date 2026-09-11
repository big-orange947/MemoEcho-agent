# -*- coding: utf-8 -*-
"""信号提取测试(app/signals.py)。

这一层是"消息里有没有事件"的地基: 时间解析错一个字,后面的事件判定
就会把"改天再说"当成排期变更,或者漏掉"明天组会改到四点"。
所以时间相关的用例写得比较细 —— 每条都对应聊天里真实出现过的说法。

不依赖网络/模型/数据库: 纯函数。
"""
from __future__ import annotations

from datetime import datetime

import pytest

from app.signals import (
    _cn_number,
    _tz,
    extract_actions,
    extract_all,
    extract_amounts,
    has_time_intent,
    nearest_time,
    normalize,
    parse_times,
    shape,
)

# 固定"现在": 2026-09-11(周五)19:00 —— 时间解析依赖当前时刻,
# 不固定的话测试会在半夜和早上给出不同结果。
NOW = datetime(2026, 9, 11, 19, 0, tzinfo=_tz())


def first_time(text: str):
    times = parse_times(text, now=NOW)
    return times[0] if times else None


# ---------------------------------------------------------------------------
# 归一化
# ---------------------------------------------------------------------------
class TestNormalize:
    def test_fullwidth_to_halfwidth(self):
        assert normalize("９点！！！") == "9点!!!"

    def test_removes_zero_width(self):
        assert normalize("好\u200b的") == "好的"

    def test_collapses_whitespace(self):
        assert normalize("明天   下午\n三点") == "明天 下午 三点"


# ---------------------------------------------------------------------------
# 时间解析
# ---------------------------------------------------------------------------
class TestParseTimes:
    def test_relative_day(self):
        item = first_time("明天见")
        assert item["date"] == "2026-09-12"
        assert item["resolved"] is True

    def test_day_with_clock_merges(self):
        """"明天下午三点"必须解析成**一个**时间,而不是"明天"+"三点"两个。"""
        times = parse_times("明天下午三点见", now=NOW)
        assert len(times) == 1
        assert times[0]["clock"] == "15:00"
        assert times[0]["raw"] == "明天三点"

    def test_chinese_numerals(self):
        """群里更常写"七点半""三点",不是阿拉伯数字。"""
        assert first_time("三点见")["clock"] == "15:00"
        assert first_time("十二点吃饭")["clock"] == "12:00"
        assert first_time("晚上七点半开黑")["clock"] == "19:30"

    def test_bare_clock_rolls_forward_when_passed(self):
        """裸时刻已经过去了 → 按明天理解(不然会算出一个负数小时数)。

        "七点半出发"在晚上七点说出口,指的只能是明早七点半。
        """
        item = first_time("七点半出发")
        assert item["clock"] == "07:30"
        assert item["hours"] > 0

    def test_night_period_word(self):
        """"今晚"既是"今天"也是"晚上" —— 少认一个都会把时间甩到明天。"""
        item = first_time("今晚七点开黑")
        assert item["date"] == "2026-09-11"
        assert item["clock"] == "19:00"

    def test_same_day_context_disambiguates_bare_clock(self):
        """"今晚七点…改到八点半": 八点半是**今晚**的八点半,不是明早的。"""
        times = parse_times("我们约的今晚七点，现在改到八点半行吗", now=NOW)
        clocks = {item["clock"] for item in times}
        assert clocks == {"19:00", "20:30"}

    def test_absolute_date(self):
        item = first_time("9月12号上午9点半面试")
        assert item["date"] == "2026-09-12"
        assert item["clock"] == "09:30"

    def test_weekday(self):
        assert first_time("周五交材料")["date"] == "2026-09-11"       # 本周五(就是今天)
        assert first_time("下周三之前交")["date"] == "2026-09-16"     # 下周三

    def test_longest_day_word_wins(self):
        """"大后天"里含"后天" —— 不去重会算成两天。"""
        times = parse_times("大后天再说", now=NOW)
        assert len(times) == 1
        assert times[0]["raw"] == "大后天"
        assert times[0]["date"] == "2026-09-14"

    def test_deadline_marker_marks_time(self):
        item = first_time("周五截止")
        assert item["deadline"] is True

    def test_fuzzy_deadline_is_unresolved(self):
        """模糊期限("月底")要能被识别成时间意图,但不该编出一个具体日期。"""
        item = first_time("月底前交报告")
        assert item["resolved"] is False
        assert item["deadline"] is True

    def test_time_question_is_intent_only(self):
        item = first_time("几点上课?")
        assert item["question"] is True
        assert item["resolved"] is False

    def test_hours_computed_from_now(self):
        """算得出"离现在还有多久",后面的紧迫度判断全靠它。"""
        item = first_time("明天下午三点见")
        assert 19 <= item["hours"] <= 21      # 明天 15:00 距离今晚 19:00 = 20 小时

    def test_no_false_positive_on_idioms(self):
        """"有一点事""差一点"里的"一点"不是时间 —— 误判会凭空造出一个时间点。"""
        assert parse_times("我有一点事想问你", now=NOW) == []
        assert parse_times("差一点就忘了", now=NOW) == []

    def test_no_time_in_plain_chat(self):
        assert parse_times("哈哈哈", now=NOW) == []
        assert parse_times("改天再聊", now=NOW) == []

    def test_multiple_times(self):
        times = parse_times("后天大后天都行", now=NOW)
        assert [item["raw"] for item in times] == ["后天", "大后天"]

    def test_past_day_is_resolved(self):
        """"昨天"也是时间(可能是"事情已经发生"),要有值,不能崩。"""
        item = first_time("昨天说的那事")
        assert item["date"] == "2026-09-10"
        assert item["hours"] < 0


class TestCnNumber:
    @pytest.mark.parametrize(
        "raw,expected",
        [("七", 7), ("十", 10), ("十一", 11), ("两", 2), ("二十", 20), ("三十", 30),
         ("二十五", 25), ("12", 12), ("", None), ("unknown", None), ("几", None)],
    )
    def test_values(self, raw, expected):
        assert _cn_number(raw) == expected


# ---------------------------------------------------------------------------
# 动作 / 语义信号
# ---------------------------------------------------------------------------
class TestActions:
    def test_change_detected(self):
        assert "改到" in [item["word"] for item in extract_actions("改到四点了")["change"]]

    def test_negated_change_becomes_settled(self):
        """"不改了"不是"要改" —— 语义正好相反,混为一谈会把定下来的事报成变更。"""
        actions = extract_actions("不改了，照旧")
        assert actions["change"] == []
        assert actions["settled"]

    def test_money_and_amount(self):
        assert extract_actions("借我点钱")["money"]
        amounts = extract_amounts("借我50块钱")
        assert [item["raw"] for item in amounts] == ["50块"]

    def test_request_and_invite(self):
        assert extract_actions("帮我拿一下")["request"]
        assert extract_actions("今晚一起打球吗")["invite"]

    def test_urgency_and_commitment(self):
        assert extract_actions("尽快回我")["urgency"]
        assert extract_actions("别忘了带材料")["commitment"]

    def test_suppress_words(self):
        """"没事了/不用管"是明确的消解表达,要能被识别(用来降分)。"""
        assert extract_actions("没事了，不用管")["suppress"]

    def test_question_keeps_legacy_prefix(self):
        """疑问词沿用 pattern: 前缀 —— 通知里展示的命中原因别变来变去。"""
        assert extract_actions("几点到?")["question"]


# ---------------------------------------------------------------------------
# 形态
# ---------------------------------------------------------------------------
class TestShape:
    def test_media_only(self):
        result = shape("[图片]")
        assert result["has_media"] is True
        assert result["media_only"] is True

    def test_media_with_text(self):
        result = shape("@123 看下[图片]")
        assert result["has_media"] is True
        assert result["media_only"] is False

    def test_plain_text(self):
        assert shape("在吗")["has_media"] is False


# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------
class TestExtractAll:
    def test_bundles_everything(self):
        signals = extract_all("明天组会改到四点", now=NOW)
        assert signals["times"] and signals["actions"]["change"] and signals["shape"]
        assert has_time_intent(signals) is True

    def test_nearest_time_picks_closest(self):
        signals = extract_all("今晚七点见，或者明天下午三点也行", now=NOW)
        assert nearest_time(signals)["clock"] == "19:00"

    def test_nearest_time_none_when_unresolved(self):
        signals = extract_all("月底前交", now=NOW)
        assert nearest_time(signals) is None
