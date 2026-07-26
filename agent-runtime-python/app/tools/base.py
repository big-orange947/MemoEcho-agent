from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ToolSpec:
    """描述工具的权限边界，供编排器在真正执行副作用前统一校验。"""

    name: str
    capability: str
    side_effect: bool = False
    requires_confirmation: bool = False


@dataclass(frozen=True)
class ToolExecutionContext:
    """携带一次工具调用的用户、事件、任务和授权范围，不允许 Agent 自行伪造权限。"""

    user_id: str
    event_id: str
    task_id: str = ""
    allowed_tools: frozenset[str] = field(default_factory=frozenset)
    trusted_internal: bool = False
