# =============================================================================
# agent/runtime.py - 图实例的全局访问点
# -----------------------------------------------------------------------------
# 为什么需要:
#   AgentGraph 在 main.create_app() 里组装(需要注入 LLM 工厂/工具/发送器),
#   但 API 路由、调度入口等模块也需要访问它(例如"这个会话忙不忙"的预检)。
#   与其到处传参,不如在组装时登记一次、需要时取用。
#
# 与 get_bus() 的关系: 同样的模式 —— 单进程内单例,生命周期由 create_app 管理。
# =============================================================================

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # 仅类型检查时导入,避免模块循环依赖
    from .graph import AgentGraph

_graph: "AgentGraph | None" = None


def set_graph(graph: "AgentGraph") -> None:
    """登记全局图实例(由 main.create_app 在组装时调用)。"""
    global _graph
    _graph = graph


def get_graph() -> "AgentGraph | None":
    """返回全局图实例(未初始化时为 None)。"""
    return _graph
