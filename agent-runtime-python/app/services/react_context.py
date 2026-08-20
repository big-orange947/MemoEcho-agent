from __future__ import annotations

from typing import Iterable

from app.schemas.react_protocol import CandidateReplyGuardResult


class CandidateReplyGuard:
    """阻止模型把主控台内部定位信息直接写入对外候选回复。"""

    def validate(self, content: str, internal_terms: Iterable[str]) -> CandidateReplyGuardResult:
        """检查候选内容是否包含动态提取的控制术语，而非维护硬编码禁词表。"""
        normalized = " ".join(str(content or "").split())
        leaked = tuple(term for term in {" ".join(str(item or "").split()) for item in internal_terms} if len(term) >= 2 and term in normalized)
        if leaked:
            return CandidateReplyGuardResult(False, tuple(f"候选回复包含内部会话定位术语：{term}" for term in leaked))
        return CandidateReplyGuardResult(True)
