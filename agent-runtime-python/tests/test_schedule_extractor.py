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


if __name__ == "__main__":
    unittest.main()

