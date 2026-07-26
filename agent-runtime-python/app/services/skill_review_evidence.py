from __future__ import annotations

import re

from app.schemas.skills import SkillDescriptor


class SkillReviewEvidenceBuilder:
    """把面向生成的长 Skill 转换为审批阶段所需的紧凑证据。"""

    DEFAULT_MAX_CHARS = 4200
    _PRIORITY_TERMS = (
        "必须",
        "不得",
        "禁止",
        "只允许",
        "仅限",
        "条件",
        "前提",
        "边界",
        "授权",
        "事实",
        "流程",
        "步骤",
        "决策",
        "价格",
        "金额",
        "联系方式",
        "身份",
        "分数",
        "省份",
        "哪个省",
        "什么省",
        "选科",
        "多少分",
        "专业",
        "家庭",
        "目标",
    )

    @classmethod
    def build(cls, skills: list[SkillDescriptor], max_chars: int | None = None) -> str:
        """按总字符预算汇总多个 Skill，避免审批请求重复携带完整长文档。"""
        usable_skills = [
            skill for skill in skills if skill.prompt_fragments.system.strip()
        ]
        if not usable_skills:
            return ""

        total_budget = max(int(max_chars or cls.DEFAULT_MAX_CHARS), 800)
        per_skill_budget = max(total_budget // len(usable_skills), 600)
        fragments: list[str] = []
        for skill in usable_skills:
            metadata = f"[Skill: {skill.name}]"
            if skill.description.strip():
                metadata += f"\n用途：{cls.compact_text(skill.description, 240)}"
            compact_prompt = cls.compact_text(
                skill.prompt_fragments.system,
                per_skill_budget - len(metadata) - 2,
            )
            fragments.append(f"{metadata}\n{compact_prompt}".strip())

        return cls._fit_to_budget("\n\n".join(fragments), total_budget)

    @classmethod
    def compact_text(cls, text: str, max_chars: int) -> str:
        """优先保留标题、开头说明及含约束关键词的段落，并维持原始顺序。"""
        normalized = str(text or "").replace("\r\n", "\n").strip()
        if len(normalized) <= max_chars:
            return normalized
        if max_chars <= 80:
            return cls._fit_to_budget(normalized, max_chars)

        blocks = cls._split_blocks(normalized)
        if not blocks:
            return cls._fit_to_budget(normalized, max_chars)

        scored_blocks: list[tuple[int, int, str]] = []
        for index, block in enumerate(blocks):
            score = cls._score_block(block, index)
            scored_blocks.append((score, index, block))

        # 先按信息价值挑选，再按原文顺序拼接，避免规则被重排后改变语义。
        selected_indexes: set[int] = set()
        used_chars = 0
        for _, index, block in sorted(scored_blocks, key=lambda item: (-item[0], item[1])):
            block_cost = len(block) + 2
            if selected_indexes and used_chars + block_cost > max_chars:
                continue
            selected_indexes.add(index)
            used_chars += block_cost
            if used_chars >= max_chars * 0.9:
                break

        selected = "\n\n".join(
            blocks[index] for index in sorted(selected_indexes)
        )
        return cls._fit_to_budget(selected, max_chars)

    @staticmethod
    def _split_blocks(text: str) -> list[str]:
        """把 Markdown Skill 拆成可独立评分的段落，并丢弃空代码围栏。"""
        blocks: list[str] = []
        for raw_block in re.split(r"\n\s*\n", text):
            block = raw_block.strip()
            if not block or block in {"```", "```text", "```markdown"}:
                continue
            blocks.append(block)
        return blocks

    @classmethod
    def _score_block(cls, block: str, index: int) -> int:
        """为段落计算审批价值，约束、显式事实和文档开头拥有更高优先级。"""
        score = 6 if index < 2 else 0
        if block.lstrip().startswith("#"):
            score += 5
        score += sum(3 for term in cls._PRIORITY_TERMS if term in block)
        if re.search(r"\d", block):
            score += 1
        if any(marker in block for marker in ("安全", "风险", "拒绝", "确认")):
            score += 3
        return score

    @staticmethod
    def _fit_to_budget(text: str, max_chars: int) -> str:
        """执行最终硬限制，并用明确标记说明内容经过压缩而非原文结束。"""
        compact = str(text or "").strip()
        if len(compact) <= max_chars:
            return compact
        suffix = "\n[其余非关键说明已省略]"
        keep = max(max_chars - len(suffix), 0)
        return compact[:keep].rstrip() + suffix
