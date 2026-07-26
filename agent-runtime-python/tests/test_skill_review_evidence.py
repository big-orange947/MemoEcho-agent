from __future__ import annotations

import unittest

from app.schemas.skills import SkillDescriptor
from app.services.skill_review_evidence import SkillReviewEvidenceBuilder


class SkillReviewEvidenceBuilderTest(unittest.TestCase):
    """验证长 Skill 在审查链路中只保留关键证据且不会撑爆模型输入。"""

    def test_should_compact_long_skill_and_keep_critical_constraints(self) -> None:
        """压缩后应保留显式规则与业务事实，并严格遵守字符预算。"""
        filler = "普通背景介绍，不影响审批判断。" * 90
        prompt = (
            "# 志愿咨询 Skill\n\n"
            f"{filler}\n\n"
            "## 必须遵守\n必须先确认分数、哪个省和选科，不得编造家庭情况。\n\n"
            f"{filler}\n\n"
            "已知价格为每次 15 元，只允许在用户明确确认后继续。"
        )
        skill = SkillDescriptor(
            id="test.skill",
            name="测试 Skill",
            description="用于测试长提示词压缩",
            promptFragments={"system": prompt},
        )

        evidence = SkillReviewEvidenceBuilder.build([skill], max_chars=900)

        self.assertLessEqual(len(evidence), 900)
        self.assertIn("必须先确认分数、哪个省和选科", evidence)
        self.assertIn("不得编造家庭情况", evidence)
        self.assertIn("每次 15 元", evidence)

    def test_should_keep_each_skill_name_when_multiple_skills_are_loaded(self) -> None:
        """多 Skill 场景必须保留来源名称，避免审批模型混淆证据出处。"""
        skills = [
            SkillDescriptor(
                id=f"skill-{index}",
                name=f"Skill {index}",
                promptFragments={"system": "必须遵守当前会话边界。"},
            )
            for index in range(2)
        ]

        evidence = SkillReviewEvidenceBuilder.build(skills, max_chars=900)

        self.assertIn("[Skill: Skill 0]", evidence)
        self.assertIn("[Skill: Skill 1]", evidence)


if __name__ == "__main__":
    unittest.main()
