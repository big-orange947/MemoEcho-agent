from __future__ import annotations

import unittest

from app.planner.service import PlannerService


class PlannerServiceTest(unittest.TestCase):
    def test_social_reply_runs_context_review_before_final_review(self) -> None:
        """社交回复必须先做情景一致性审查，再进入最终闭世界审批。"""
        plan = PlannerService().build_plan("social_reply")

        self.assertEqual(
            [step.agent for step in plan.steps],
            ["inbox_dispatch", "social", "context_review", "review"],
        )
