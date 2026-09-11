# =============================================================================
# agent/runtime.py - 运行时组件的全局访问点(图实例 + 发送器)
# -----------------------------------------------------------------------------
# 为什么需要:
#   这些组件在 main.create_app() 里组装(需要注入 LLM 工厂/工具/发送器),
#   但 API 路由、调度入口、outbox 等模块也需要访问它们
#   (例如"这个会话忙不忙"的预检、"直接发送一条消息")。
#   与其到处传参,不如在组装时登记一次、需要时取用。
#
# 与 get_bus() 的关系: 同样的模式 —— 单进程内单例,生命周期由 create_app 管理。
# =============================================================================

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Awaitable, Callable

if TYPE_CHECKING:  # 仅类型检查时导入,避免模块循环依赖
    from .graph import AgentGraph

_graph: "AgentGraph | None" = None

# 消息发送器: (conversation_id, text, source) -> None
# 由 main.create_app 注入;outbox 用它投递出站消息。
_sender: Callable[[str, str, str], Awaitable[Any]] | None = None

# 事件推送器(SSE): (event_type, payload) -> None
# 由 main.create_app 注入。核心层(agent 节点)用它把**面向界面**的通知
# (如"拟好了一条待确认草稿")发出去 —— 核心层不直接依赖 api 层,
# 推送是尽力而为: 没登记或推送失败都不影响业务(队列才是权威通道)。
_notifier: Callable[[str, dict[str, Any]], Awaitable[Any]] | None = None


def set_graph(graph: "AgentGraph") -> None:
    """登记全局图实例(由 main.create_app 在组装时调用)。"""
    global _graph
    _graph = graph


def get_graph() -> "AgentGraph | None":
    """返回全局图实例(未初始化时为 None)。"""
    return _graph


def set_sender(sender: Callable[[str, str, str], Awaitable[Any]]) -> None:
    """登记全局消息发送器(由 main.create_app 在组装时调用)。"""
    global _sender
    _sender = sender


def get_sender() -> Callable[[str, str, str], Awaitable[Any]] | None:
    """返回全局消息发送器(未初始化时为 None)。"""
    return _sender


def set_notifier(notifier: Callable[[str, dict[str, Any]], Awaitable[Any]]) -> None:
    """登记全局事件推送器(由 main.create_app 在组装时调用)。"""
    global _notifier
    _notifier = notifier


async def notify(event_type: str, payload: dict[str, Any]) -> None:
    """尽力推送一条面向界面的通知。

    刻意**不**向上抛异常: 推送只是"让人早点看见",丢了还有队列兜底
    (消费者可以 claim / 长轮询)。让通知失败拖垮一次业务执行是本末倒置。
    """
    if _notifier is None:
        return
    try:
        await _notifier(event_type, payload)
    except Exception as exc:  # noqa: BLE001 - 通知不是关键路径
        print(f"[notify] 推送失败 {event_type}: {type(exc).__name__}: {exc}")
