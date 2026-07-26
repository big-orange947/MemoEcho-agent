from datetime import datetime
import unittest

from app.agents.schedule_extractor import ScheduleExtractor


class ScheduleExtractorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.extractor = ScheduleExtractor()
        self.reference = datetime(2026, 7, 6, 10, 0, 0)

    def test_should_extract_single_time_and_location(self) -> None:
        text = "【考研经验分享会】今天下午14:00在A01-N105举办分享会，欢迎感兴趣的同学参加。"

        candidate = self.extractor.extract(text, now=self.reference)

        self.assertEqual(candidate.title, "考研经验分享会")
        self.assertEqual(candidate.start_time, "2026-07-06 14:00:00")
        self.assertIsNone(candidate.end_time)
        self.assertEqual(candidate.location, "A01-N105")
        self.assertEqual(candidate.participants, "感兴趣的同学参加")
        self.assertEqual(candidate.confidence, "high")

    def test_should_extract_time_range(self) -> None:
        text = "【节能减排大赛路演】各位同学，中午12:00-14:00将有参赛队伍现场演示，地点B12彩虹长廊侧广场。"

        candidate = self.extractor.extract(text, now=self.reference)

        self.assertEqual(candidate.title, "节能减排大赛路演")
        self.assertEqual(candidate.start_time, "2026-07-06 12:00:00")
        self.assertEqual(candidate.end_time, "2026-07-06 14:00:00")
        self.assertEqual(candidate.location, "B12彩虹长廊侧广场")
        self.assertEqual(candidate.participants, "各位同学")

    def test_should_stop_location_before_activity_verb(self) -> None:
        # 这个测试函数的作用是验证地点提取不会把“开项目例会”或“参加讲座”等活动描述吞入地点字段。
        meeting = self.extractor.extract("明天下午两点在A01-N105开项目例会", now=self.reference)
        lecture = self.extractor.extract("7月18日下午三点在B12报告厅参加人工智能讲座", now=self.reference)

        self.assertEqual(meeting.location, "A01-N105")
        self.assertEqual(lecture.location, "B12报告厅")

    def test_should_resolve_next_week_and_chinese_clock(self) -> None:
        # 这个测试函数的作用是验证“下周三下午三点半”会按消息发生时间确定到唯一日期和时间。
        text = "【论文讨论】下周三下午三点半在A01-N105开会。"

        candidate = self.extractor.extract(text, now=self.reference)

        self.assertEqual(candidate.start_time, "2026-07-15 15:30:00")
        self.assertEqual(candidate.location, "A01-N105")
        self.assertTrue(candidate.date_is_explicit)
        self.assertTrue(candidate.time_is_explicit)
        self.assertFalse(candidate.ambiguous)

    def test_should_mark_invalid_calendar_date_as_ambiguous(self) -> None:
        # 这个测试函数的作用是验证非法年月日不会被静默修正成其他日期。
        candidate = self.extractor.extract("2月30日14:00开会", now=self.reference)

        self.assertTrue(candidate.ambiguous)

    def test_should_prefer_explicit_event_date_over_message_time_word(self) -> None:
        # 这个测试函数的作用是验证“今天通知”不会覆盖后文明确写出的活动日期。
        candidate = self.extractor.extract(
            "今天通知大家，2026年7月20日14:00在A01开会",
            now=datetime(2026, 7, 15, 9, 0),
        )

        self.assertEqual(candidate.start_time, "2026-07-20 14:00:00")
        self.assertFalse(candidate.ambiguous)

    def test_should_mark_alternative_or_multiple_dates_as_ambiguous(self) -> None:
        # 这个测试函数的作用是验证二选一日期和同消息多事件日期都不能被规则直接确认。
        alternative = self.extractor.extract(
            "明天或者后天14:00开会",
            now=datetime(2026, 7, 15, 9, 0),
        )
        multiple = self.extractor.extract(
            "2026年7月20日14:00开会，2026年7月21日15:00复盘",
            now=datetime(2026, 7, 15, 9, 0),
        )

        self.assertTrue(alternative.ambiguous)
        self.assertTrue(multiple.ambiguous)

    def test_should_mark_separate_multiple_times_as_ambiguous(self) -> None:
        # 这个测试函数的作用是验证非时间范围内出现多个时刻时不会只取第一个后直接落库。
        candidate = self.extractor.extract(
            "明天14:00开会，17:00提交报告",
            now=datetime(2026, 7, 15, 9, 0),
        )

        self.assertTrue(candidate.ambiguous)
        self.assertEqual(candidate.confidence, "medium")


if __name__ == "__main__":
    unittest.main()
