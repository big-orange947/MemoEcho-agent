from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.skills.resolver import SkillResolver


class SkillResolverTest(unittest.TestCase):
    def setUp(self) -> None:
        # 这个函数的作用是为每个测试同时准备：
        # 1. 仓库内置的 skills 根目录
        # 2. 临时的 skills-installed 目录
        # 这样既能验证本地 skill，也能验证“GitHub skill 安装后再解析”的链路。
        self.skill_root = Path(__file__).resolve().parents[1] / "skills"
        self.temp_dir = tempfile.TemporaryDirectory()
        self.installed_root = Path(self.temp_dir.name)
        self.resolver = SkillResolver([self.skill_root, self.installed_root])

    def tearDown(self) -> None:
        # 这个函数的作用是清理测试过程中创建的临时已安装 skill 缓存目录，避免污染本地工作区。
        self.temp_dir.cleanup()

    def test_should_resolve_local_skill_directory_reference(self) -> None:
        # 这个测试函数的作用是验证本地目录形式的 skill 引用会被自动定位到对应的 skill.json。
        resolved_skills, unresolved = self.resolver.resolve_references(
            ["skills/personas/reliable-assistant"],
            route="social_reply",
        )

        self.assertEqual(unresolved, [])
        self.assertEqual(len(resolved_skills), 1)
        self.assertEqual(resolved_skills[0].id, "persona.reliable_assistant")
        self.assertEqual(resolved_skills[0].tool_policy.allow, ["send_qq_message"])
        self.assertTrue(resolved_skills[0].prompt_fragments.system)

    def test_should_filter_skill_when_route_does_not_match(self) -> None:
        # 这个测试函数的作用是验证 skill 的 applicableRoutes 不包含当前 route 时，不会把该 skill 注入到运行结果里。
        resolved_skills, unresolved = self.resolver.resolve_references(
            ["skills/work/project-manager"],
            route="social_reply",
        )

        self.assertEqual(unresolved, [])
        self.assertEqual(resolved_skills, [])

    def test_should_resolve_installed_github_skill_from_local_cache(self) -> None:
        # 这个测试函数的作用是验证 GitHub skill 一旦被安装到本地缓存目录，runtime 就能直接按 github:// 引用完成解析。
        install_dir = self.installed_root / "github" / "demo-owner" / "demo-repo" / "main" / "personas" / "reliable-assistant"
        install_dir.mkdir(parents=True, exist_ok=True)
        (install_dir / "skill.json").write_text(
            json.dumps(
                {
                    "id": "github.demo.reliable_assistant",
                    "name": "GitHub 可靠助理人格",
                    "version": "1.0.0",
                    "type": "persona",
                    "description": "用于验证已安装 GitHub skill 的解析链路",
                    "source": "github",
                    "applicableRoutes": ["social_reply"],
                    "promptFragments": {
                        "system": "回复时保持冷静、可靠、克制。"
                    },
                    "toolPolicy": {
                        "allow": ["send_qq_message"]
                    },
                    "modelHints": {
                        "temperature": 0.4,
                        "maxTokens": 512
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        resolved_skills, unresolved = self.resolver.resolve_references(
            ["github://demo-owner/demo-repo/personas/reliable-assistant"],
            route="social_reply",
        )

        self.assertEqual(unresolved, [])
        self.assertEqual(len(resolved_skills), 1)
        self.assertEqual(resolved_skills[0].source, "github")
        self.assertEqual(resolved_skills[0].raw_reference, "github://demo-owner/demo-repo/personas/reliable-assistant")
        self.assertEqual(resolved_skills[0].tool_policy.allow, ["send_qq_message"])

    def test_should_mark_github_reference_as_unresolved_when_not_installed(self) -> None:
        # 这个测试函数的作用是验证尚未安装到本地缓存目录的 GitHub skill 会被明确标记为未解析，而不是静默忽略。
        resolved_skills, unresolved = self.resolver.resolve_references(
            ["github://demo/repo/skills/reliable-assistant@main"],
            route="social_reply",
        )

        self.assertEqual(resolved_skills, [])
        self.assertEqual(unresolved, ["github://demo/repo/skills/reliable-assistant@main"])

    def test_should_resolve_installed_github_skill_from_repository_root(self) -> None:
        # 这个测试函数的作用是验证标准 Agent Skills 仓库根目录安装后，可通过不带子路径的规范引用加载。
        install_dir = self.installed_root / "github" / "demo-owner" / "root-skill" / "main" / "root"
        install_dir.mkdir(parents=True, exist_ok=True)
        (install_dir / "skill.json").write_text(
            json.dumps(
                {
                    "id": "github.demo.root_skill",
                    "name": "仓库根目录 Skill",
                    "version": "1.0.0",
                    "type": "prompt",
                    "source": "github",
                    "applicableRoutes": ["social_reply"],
                    "promptFragments": {"system": "只使用已知信息回复。"},
                    "toolPolicy": {"allow": []},
                    "modelHints": {},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        resolved_skills, unresolved = self.resolver.resolve_references(
            ["github://demo-owner/root-skill@main"],
            route="social_reply",
        )

        self.assertEqual(unresolved, [])
        self.assertEqual(len(resolved_skills), 1)
        self.assertEqual(resolved_skills[0].name, "仓库根目录 Skill")


if __name__ == "__main__":
    unittest.main()
