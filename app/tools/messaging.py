# =============================================================================
# tools/messaging.py - 消息发送工具
# -----------------------------------------------------------------------------
# 让 LLM 可以"主动给其他人发消息"(比如转告小号、催问别人)。
# 注意: 回复"当前会话"不需要这个工具 —— finalize 节点统一发送,
# 避免 LLM 自己调发送工具造成重复/错乱(这是 v1 踩过的坑)。
#
# 工具通过依赖注入拿到"发送器"(main.py 注入 QQ 桥/桌面端实现),
# 这样本模块不直接依赖 NapCat,方便测试和替换渠道。
# =============================================================================

from __future__ import annotations

from typing import Any, Callable, Coroutine

from langchain_core.tools import tool

# 发送器类型: (platform, chat_type, external_id, text) -> None
# 由 main.py 注入,内部路由到 QQ 桥或桌面端
MessageSender = Callable[[str, str, str, str], Coroutine[Any, Any, None]]

# 全局发送器(在 create_message_tools 里注入)
_sender: MessageSender | None = None


def init_sender(sender: MessageSender) -> None:
    """注入消息发送器(应用启动时调用一次)。"""
    global _sender
    _sender = sender


@tool
def send_qq_message(chat_id: str, text: str) -> str:
    """向指定 QQ 联系人发送一条私聊消息。

    chat_id: 对方 QQ 号(字符串形式)
    text:    要发送的消息内容
    返回: 发送结果描述,成功或失败原因。
    """
    if _sender is None:
        return "错误: 消息发送器未初始化"
    try:
        # 这里走 asyncio 事件循环执行异步发送器
        import asyncio

        asyncio.get_running_loop().create_task(
            _sender("qq", "private", chat_id, text)
        )
        return f"已向 {chat_id} 发送消息"
    except Exception as exc:  # noqa: BLE001
        return f"发送失败: {type(exc).__name__}: {exc}"


def create_message_tools() -> list[Any]:
    """返回消息类工具列表(当前只有一个)。"""
    return [send_qq_message]
