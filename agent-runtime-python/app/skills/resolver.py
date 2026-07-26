from __future__ import annotations

import json
import os
from pathlib import Path

from app.schemas.skills import SkillDescriptor


class SkillResolver:
    def __init__(self, skill_roots: list[Path]) -> None:
        # 这个构造函数的作用是保存 skill 搜索根目录列表。
        # 后续无论是内置 skill 还是已安装的 GitHub skill，都会先映射到这些根目录下查找对应的 skill.json。
        self.skill_roots = skill_roots

    @classmethod
    def build_default(cls) -> "SkillResolver":
        # 这个函数的作用是构造默认 skill 搜索路径。
        # 它同时覆盖两类目录：
        # 1. 仓库内置的 `skills/`
        # 2. GitHub skill 安装后写入的 `skills-installed/`
        roots: list[Path] = []

        configured_root = (os.getenv("MEMO_ECHO_SKILLS_DIR") or "").strip()
        if configured_root:
            roots.append(Path(configured_root).resolve())

        configured_installed_root = (os.getenv("MEMO_ECHO_SKILLS_INSTALLED_DIR") or "").strip()
        if configured_installed_root:
            roots.append(Path(configured_installed_root).resolve())

        project_skill_root = Path(__file__).resolve().parents[2] / "skills"
        project_installed_root = Path(__file__).resolve().parents[2] / "skills-installed"
        roots.append(project_skill_root.resolve())
        roots.append(project_installed_root.resolve())

        unique_roots: list[Path] = []
        seen: set[str] = set()
        for root in roots:
            normalized = str(root)
            if normalized in seen:
                continue
            seen.add(normalized)
            unique_roots.append(root)
        return cls(unique_roots)

    def resolve_references(
        self,
        references: list[str],
        route: str | None = None,
    ) -> tuple[list[SkillDescriptor], list[str]]:
        # 这个函数的作用是批量解析会话设定中的 skill 引用。
        # 它不仅返回成功解析出的 skill，也会把失败的引用单独收集出来，方便上层前端或日志直接解释“为什么这个 skill 没生效”。
        resolved_skills: list[SkillDescriptor] = []
        unresolved_references: list[str] = []

        for reference in references:
            normalized_reference = str(reference or "").strip()
            if not normalized_reference:
                continue

            descriptor = self._resolve_single_reference(normalized_reference)
            if descriptor is None:
                unresolved_references.append(normalized_reference)
                continue

            if route and descriptor.applicable_routes and route not in descriptor.applicable_routes:
                continue
            resolved_skills.append(descriptor)

        return resolved_skills, unresolved_references

    def _resolve_single_reference(self, reference: str) -> SkillDescriptor | None:
        # 这个函数的作用是解析单个 skill 引用。
        # 这里故意坚持“只读本地文件”的原则：
        # - 本地 skill 直接读内置目录
        # - GitHub skill 只读已经安装到本地缓存目录的 skill.json
        # 这样 runtime 不会在执行消息时临时联网拉取远程内容。
        candidate_paths = self._github_candidate_paths(reference) if reference.startswith("github://") else self._candidate_paths(reference)

        for candidate in candidate_paths:
            if not candidate.exists() or not candidate.is_file():
                continue
            try:
                payload = json.loads(candidate.read_text(encoding="utf-8"))
                return SkillDescriptor.model_validate(
                    {
                        **payload,
                        "source": payload.get("source", "local"),
                        "rawReference": reference,
                    }
                )
            except Exception:
                continue
        return None

    def _candidate_paths(self, reference: str) -> list[Path]:
        # 这个函数的作用是把本地 skill 引用扩展成多个候选路径。
        # 兼容以下几种写法：
        # - `skills/personas/reliable-assistant`
        # - `local://personas/reliable-assistant`
        # - `skills/.../skill.json`
        normalized = reference.removeprefix("local://").strip().replace("\\", "/")
        relative = normalized.removeprefix("./")
        candidates: list[Path] = []

        for root in self.skill_roots:
            if relative.startswith("skills/"):
                candidate = root / relative.removeprefix("skills/")
            else:
                candidate = root / relative

            if candidate.suffix.lower() == ".json":
                candidates.append(candidate)
            else:
                candidates.append(candidate / "skill.json")
                candidates.append(candidate)
        return candidates

    def _github_candidate_paths(self, reference: str) -> list[Path]:
        # 这个函数的作用是把 github:// 引用映射成本地缓存目录里的 skill.json 路径。
        # 只要 Java 侧已经完成“下载并安装”，runtime 就能像读取本地 skill 一样读取 GitHub skill。
        parsed = self._parse_github_reference(reference)
        if parsed is None:
            return []

        owner, repository, git_ref, descriptor_path = parsed
        if descriptor_path.endswith(".json"):
            folder_path = descriptor_path.rsplit("/", 1)[0] if "/" in descriptor_path else "root"
        else:
            folder_path = descriptor_path
        folder_path = folder_path.strip("/") or "root"

        candidates: list[Path] = []
        for root in self.skill_roots:
            candidates.append(
                root
                / "github"
                / owner
                / repository
                / git_ref
                / Path(folder_path)
                / "skill.json"
            )
        return candidates

    def _parse_github_reference(self, reference: str) -> tuple[str, str, str, str] | None:
        # 这个函数的作用是解析 GitHub skill 引用的关键组成部分。
        # 当前同时支持仓库根目录和子目录两种写法：
        # - `github://owner/repo@main`
        # - `github://owner/repo@main/path/to/skill-dir`
        normalized = str(reference or "").strip()
        if not normalized.startswith("github://"):
            return None

        without_scheme = normalized.removeprefix("github://")
        segments = without_scheme.split("/", 3)
        if len(segments) < 2:
            return None

        owner = segments[0].strip()
        repository_with_ref = segments[1].strip()
        descriptor_path = ""
        if len(segments) == 3:
            descriptor_path = segments[2].strip()
        elif len(segments) == 4:
            descriptor_path = f"{segments[2].strip()}/{segments[3].strip()}"
        if not owner or not repository_with_ref:
            return None

        repository = repository_with_ref
        git_ref = "main"
        if "@" in repository_with_ref:
            repository, git_ref = repository_with_ref.split("@", 1)
            repository = repository.strip()
            git_ref = git_ref.strip() or "main"

        return owner, repository, git_ref, descriptor_path.replace("\\", "/")
