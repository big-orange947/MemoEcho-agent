# =============================================================================
# events.py - 事件模型 + 事件总线
# -----------------------------------------------------------------------------
# 事件是系统的"统一入口": 无论是 QQ 消息、桌面端命令、定时器触发,
# 还是其他 agent 的调度指令,都先归一化成 Event 对象,再交给 LangGraph 代理处理。
#
# 为什么要有这一层:
#   1. 把"平台差异"挡在外面 —— agent 只认统一的 Event,不关心它来自 QQ 还是桌面;
#   2. 便于记录审计日志(events 表)和去重(相同平台消息 ID 只处理一次);
#   3. 用 kind + should_respond 做分流 —— 收到的东西不一定都要"回话":
#      撤回通知、好友申请、机器人自己的消息回显,都只记录不回应。
#
# 一个 Event 的完整构成:
#   ┌─ 身份:     event_id(幂等键), source(来源), kind(类别)
#   ├─ 分流:     should_respond(是否需要 agent 生成输出)
#   ├─ 定位:     platform / chat_type / external_id  →  conversation_id
#   ├─ 内容:     content(内容段列表) + text(渲染文本)
#   └─ 元信息:   sender_id / sender_name / is_self / raw / created_at
# =============================================================================

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine

from .content import ContentPart

# 事件处理器类型: 接收一个 Event,返回任意结果(协程)
EventHandler = Callable[["Event"], Coroutine[Any, Any, Any]]


# ---------------------------------------------------------------------------
# 事件分类常量
# ---------------------------------------------------------------------------
class EventKind:
    """事件类别。区分"要回应的消息"与"仅记录的事件"。"""

    MESSAGE = "message"              # 联系人发来的消息(通常要回应)
    MESSAGE_SENT = "message_sent"    # 机器人自己发出的消息回显(绝不回应)
    NOTICE = "notice"                # 平台通知(撤回/戳一戳/群变动…) —— 仅审计
    REQUEST = "request"              # 平台请求(好友申请/加群申请) —— 仅审计
    INSTRUCTION = "instruction"      # 其他 agent 的调度指令(建目标并推进)
    TIMER = "timer"                  # 定时唤醒(agent 自己登记的等待到期)
    COMMAND = "command"              # 桌面端命令(带 command 文本,建目标)
    SYSTEM = "system"                # 系统内部事件


class EventSource:
    """事件来源。用于审计与"同一消息不同来源"的区分。"""

    QQ = "qq"              # NapCat 桥
    DESKTOP = "desktop"    # 桌面端 API
    AGENT = "agent"        # 其他 agent 调度
    SCHEDULER = "scheduler"  # 定时调度器
    SYSTEM = "system"      # 内部系统事件


@dataclass
class Event:
    """归一化后的统一事件。

    注意区分三个"类型"字段,它们回答不同问题:
      source           —— 谁发来的?(qq / desktop / agent / scheduler)
      kind             —— 是什么性质的事件?(消息 / 通知 / 指令 / 定时)
      should_respond   —— 需要 agent 生成输出吗?(由来源侧解析器判定)
    """

    # ------------------------------------------------------------ 身份
    # 事件唯一 ID(默认自动生成)。QQ 消息用平台 message_id 保证幂等去重。
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    source: str = EventSource.SYSTEM     # 事件来源(见 EventSource)
    kind: str = EventKind.MESSAGE        # 事件类别(见 EventKind)

    # ------------------------------------------------------------ 分流
    # 是否需要 agent 生成输出。
    #   True  → 进入六节点图(落历史 → 决策 → 回复/调工具)
    #   False → 只做审计记录(如撤回通知、机器人自发回显),绝不触发回复
    # 由各来源的解析器判定(平台规则放解析器,agent 只认这个开关)。
    should_respond: bool = True

    # ------------------------------------------------------------ 会话定位
    # 与 conversations 表对应: platform + chat_type + external_id 唯一确定会话
    platform: str = "qq"          # qq / desktop / agent
    chat_type: str = "private"    # private(私聊) / group(群聊) / thread(桌面线程)
    external_id: str = ""         # QQ 号/群号/桌面线程 ID
    # 可选: 已确定的会话内部 ID(桌面端与调度入口直接携带,免去三元组查找)
    conversation_id: str = ""

    # ------------------------------------------------------------ 内容
    # 结构化内容段(文本/@/图片/文件…)。平台原始语义都在这里。
    content: list[ContentPart] = field(default_factory=list)
    # 渲染后的可读文本(如 "@123 看下[图片]"),给 LLM 与落库展示用。
    # 所有下游节点只依赖这个字段即可工作(结构化处理再按需读 content)。
    text: str = ""

    # ------------------------------------------------------------ 元信息
    sender_id: str = ""           # 发送者 ID(对方 QQ 号 / 桌面用户 / 调用方)
    sender_name: str = ""         # 发送者显示名(群名片或昵称,可空)
    is_self: bool = False         # 是否机器人自己发出的(回显,通常忽略)
    # 平台侧消息 ID(QQ 的 message_id)。
    # 用途: 识别"我们发出去的消息被平台回显"—— 发送时记下这个 ID,
    # 回显再次到达时就能跳过,避免同一条消息在历史里出现两遍。
    platform_message_id: str = ""
    raw: dict[str, Any] = field(default_factory=dict)  # 原始载荷(排障用)

    # ------------------------------------------------------------ 指令扩展
    # 调度入口 / 桌面命令可携带目标文本(如"问km几点上课转告小号"),
    # agent 会把它变成 goal 并围绕它推进。
    command: str = ""
    # 调用方上下文(主 agent 调度时透传,用于审计与结果回报)
    context: dict[str, Any] = field(default_factory=dict)

    created_at: float = field(default_factory=time.time)

    # ------------------------------------------------------------ 便捷构造
    @classmethod
    def from_text(
        cls,
        text: str,
        *,
        source: str = EventSource.DESKTOP,
        kind: str = EventKind.MESSAGE,
        platform: str = "desktop",
        chat_type: str = "thread",
        external_id: str = "",
        conversation_id: str = "",
        sender_id: str = "",
        should_respond: bool = True,
        command: str = "",
        context: dict[str, Any] | None = None,
    ) -> "Event":
        """用一段纯文本快速构造事件(桌面端/调度入口/定时器常用)。

        自动把文本包成单个 text 内容段,保证 content 与 text 一致。
        """
        parts = [ContentPart.text_part(text)] if text else []
        return cls(
            source=source,
            kind=kind,
            should_respond=should_respond,
            platform=platform,
            chat_type=chat_type,
            external_id=external_id,
            conversation_id=conversation_id,
            content=parts,
            text=text,
            sender_id=sender_id,
            command=command,
            context=context or {},
        )

    # ------------------------------------------------------------ 序列化
    def to_payload(self) -> dict[str, Any]:
        """转成可 JSON 序列化的字典。

        用途: 放进 LangGraph 的 State(会被 checkpoint 序列化),
        因此这里必须只含 JSON 原生类型 —— ContentPart 转成 dict。
        """
        return {
            "event_id": self.event_id,
            "source": self.source,
            "kind": self.kind,
            "should_respond": self.should_respond,
            "platform": self.platform,
            "chat_type": self.chat_type,
            "external_id": self.external_id,
            "conversation_id": self.conversation_id,
            "content": [part.to_dict() for part in self.content],
            "text": self.text,
            "sender_id": self.sender_id,
            "sender_name": self.sender_name,
            "is_self": self.is_self,
            "platform_message_id": self.platform_message_id,
            "command": self.command,
            "context": self.context,
            "created_at": self.created_at,
        }


class EventBus:
    """极简事件总线: 注册处理器 → 发布事件 → 按顺序派发。

    当前是单进程内同步派发;如果将来要拆多进程,可以把这里换成消息队列,
    但接口(register/publish)保持不变,上层代码无需改动。
    """

    def __init__(self) -> None:
        # 处理器列表。支持多个监听者(如"记审计日志" + "跑 agent")
        self._handlers: list[EventHandler] = []

    def register(self, handler: EventHandler) -> None:
        """注册一个事件处理器(协程函数)。"""
        self._handlers.append(handler)

    async def publish(self, event: Event) -> list[Any]:
        """把事件派发给所有处理器,按注册顺序执行。

        设计取舍:
          - **顺序执行**(而非并发): 保证"先记审计日志,再跑 agent"的相对顺序;
          - **异常隔离**: 单个处理器抛异常时记录并继续派发给后续处理器 ——
            审计日志失败不应阻断 agent 处理,agent 失败也不该吞掉审计记录。
        """
        results: list[Any] = []
        for handler in self._handlers:
            try:
                results.append(await handler(event))
            except Exception as exc:  # noqa: BLE001 - 处理器之间互相隔离
                print(
                    f"[bus] 处理器 {getattr(handler, '__name__', handler)} 处理事件 "
                    f"{event.event_id}({event.kind}) 失败: {type(exc).__name__}: {exc}"
                )
                results.append(None)
        return results


# 模块级单例总线(应用启动时注入处理器)
_bus: EventBus | None = None


def get_bus() -> EventBus:
    """返回全局事件总线单例。"""
    global _bus
    if _bus is None:
        _bus = EventBus()
    return _bus


def reset_bus() -> EventBus:
    """重建全局总线,返回新实例。

    为什么需要: 处理器是注册在**全局单例**上的,而 create_app() 可能被
    调用多次(模块级组装一次 + 测试/重载再组装一次)。若不重置,同一批
    处理器会被重复注册,导致**每个事件被处理多次**(重复回复、重复发送)。
    组装入口必须在注册处理器前先重置总线。
    """
    global _bus
    _bus = EventBus()
    return _bus
