# =============================================================================
# events.py - 事件模型 + 事件总线
# -----------------------------------------------------------------------------
# 事件是系统的"统一入口": 无论是 QQ 消息、桌面端命令还是定时器触发,
# 都先归一化成 Event 对象,再交给 LangGraph 代理处理。
#
# 为什么要有这一层:
#   1. 把"平台差异"挡在外面 —— agent 只认统一的 Event,不关心它来自 QQ 还是桌面;
#   2. 便于记录审计日志(events 表)和去重(相同平台消息 ID 只处理一次)。
# =============================================================================

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine

# 事件处理器类型: 接收一个 Event,返回任意结果(协程)
EventHandler = Callable[[ "Event" ], Coroutine[Any, Any, Any]]


@dataclass
class Event:
    """归一化后的统一事件。"""

    # 事件唯一 ID(默认自动生成);QQ 消息可用平台消息 ID 保证幂等
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    # 事件类型: message(联系人消息) / command(桌面端命令) / timer(定时) / system
    event_type: str = "message"

    # ---------------- 会话定位 ----------------
    # 与 conversations 表对应: platform + chat_type + external_id 唯一确定一个会话
    platform: str = "qq"          # qq / desktop
    chat_type: str = "private"    # private / group / thread
    external_id: str = ""         # QQ 号/群号/桌面线程 ID
    # 可选: 已确定的会话内部 ID(桌面端消息直接携带,无需再按三元组查找)
    conversation_id: str = ""

    # ---------------- 消息内容 ----------------
    text: str = ""                # 纯文本内容
    sender_id: str = ""           # 发送者 ID(对方 QQ 号/桌面用户)
    is_self: bool = False         # 是否机器人自己发出的消息(回显,通常忽略)
    raw: dict[str, Any] = field(default_factory=dict)  # 原始载荷

    # ---------------- 命令扩展 ----------------
    # 桌面端命令可携带"目标"文本(如"问km几点上课转告小号"),agent 会把它变成 goal
    command: str = ""             # 命令原文(仅 event_type=command 时有值)

    created_at: float = field(default_factory=time.time)


class EventBus:
    """极简事件总线: 注册处理器 → 发布事件 → 按顺序派发。

    当前是单进程内同步派发;如果将来要拆多进程,可以把这里换成消息队列,
    但接口(register/publish)保持不变,上层代码无需改动。
    """

    def __init__(self) -> None:
        # 处理器列表。支持多个监听者(如"记日志" + "跑 agent")
        self._handlers: list[EventHandler] = []

    def register(self, handler: EventHandler) -> None:
        """注册一个事件处理器(协程函数)。"""
        self._handlers.append(handler)

    async def publish(self, event: Event) -> list[Any]:
        """把事件派发给所有处理器,按注册顺序执行。

        注意: 处理器内部异常会向上抛出。实际使用中,建议处理器自己
        try/except 兜底,避免一个监听器失败导致整条链路中断。
        """
        results: list[Any] = []
        for handler in self._handlers:
            results.append(await handler(event))
        return results


# 模块级单例总线(应用启动时注入处理器)
_bus: EventBus | None = None


def get_bus() -> EventBus:
    """返回全局事件总线单例。"""
    global _bus
    if _bus is None:
        _bus = EventBus()
    return _bus
