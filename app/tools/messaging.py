# =============================================================================
# tools/messaging.py - 消息发送工具(给"其他人"发消息)
# -----------------------------------------------------------------------------
# 用途: 让 LLM 主动给别人发消息 —— 这是"转告/帮问"类任务的核心能力
#       (如"问小号今晚几点上课,然后转告 km")。
#
# 与 finalize 的分工:
#   · 回复**当前会话** → 由 finalize 节点统一发送,LLM 不用也不该自己调工具
#     (v1 踩过坑: 模型自己发导致重复/错乱);
#   · 发给**其他联系人** → 必须用本工具,因为 finalize 只认当前会话。
#
# 为什么是 async 工具:
#   发送是异步 IO。LangGraph 执行同步节点时会把它丢进线程池 ——
#   线程池里没有事件循环,任何"猜事件循环"的写法(如 get_running_loop)
#   都会失败。所以工具本身定义为 async,由 act 节点用 ainvoke 调用。
#
# 为什么用注入而不是直接 import NapCat:
#   保持工具层与渠道解耦: 测试时可注入假发送器;
#   将来加微信等渠道也不用改这个文件。
# =============================================================================

from __future__ import annotations

from typing import Any, Awaitable, Callable

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from ..services.policy import HIGH_RISK_TAG

# 发送器契约: (platform, chat_type, external_id, text, origin_conversation_id) -> 是否成功
#   platform   qq / desktop
#   chat_type  private / group
#   external_id 对方 QQ 号 / 群号
#   origin_conversation_id 发起本次外联的会话(用于把任务"延伸"到被联系的会话,
#                          见 services/goals.link_conversation;直接调用时可留空)
# 由 main.py 在组装时注入(见 init_sender)。
# 返回 bool 而不是抛异常: 发送失败要给 LLM 一个可理解的反馈,
# 让它能决定"告诉用户发不出去"而不是盲目重试。
ContactSender = Callable[[str, str, str, str, str], Awaitable[bool]]

_sender: ContactSender | None = None


def init_sender(sender: ContactSender) -> None:
    """注入联系人发送器(应用启动时调用一次)。

    注意: 必须在 create_app 组装阶段调用 —— 早期版本漏了这一步,
    导致工具永远返回"发送器未初始化",转告类任务全部失效。
    """
    global _sender
    _sender = sender


@tool
async def send_qq_message(
    chat_id: str, text: str, chat_type: str = "private", config: RunnableConfig = None
) -> str:
    """向指定的 QQ 联系人(或群)发送一条消息。

    这是"帮别人传话/帮问事情"的唯一方式 —— 当目标不是当前对话里的人时,
    必须用本工具发出去。

    chat_id:   对方 QQ 号,或群号
    text:      要发送的消息内容(直接写要说的话,不要带"转告他说"这类转述语)
    chat_type: "private"(私聊,默认)或 "group"(群聊)
    返回:      发送结果描述(成功或失败原因)。
    """
    if _sender is None:
        return "错误: 消息发送器未初始化(服务配置问题,请联系管理员)"

    text = (text or "").strip()
    if not text:
        return "错误: 消息内容为空,未发送"

    # ---- 兜底权限校验(第三层) ----
    # 前两层在 reason(只 bind 授权工具)与 act(拒绝未授权调用);
    # 这里再查一次"当前会话是否被允许以号主身份对外发消息",
    # 防止工具被其它入口绕过调用(群聊默认不授权,需显式开启)。
    conversation_id = str((config or {}).get("configurable", {}).get("thread_id") or "") if config else ""
    if conversation_id:
        from ..services import conversations as conversations_service
        from ..services import policy as policy_service

        conversation = conversations_service.get_conversation(conversation_id)
        if conversation and not policy_service.tool_allowed(conversation, "send_qq_message"):
            return "错误: 当前会话未授权发送消息(群聊默认关闭,需要在会话配置中开启)"

    chat_type = chat_type if chat_type in ("private", "group") else "private"

    try:
        # 把"当前会话"一并交给发送器: 它据此把这个目标延伸到被联系的会话
        # (对方的回复才能唤醒任务继续推进,见 services/goals.link_conversation)
        ok = await _sender("qq", chat_type, str(chat_id), text, conversation_id)
    except Exception as exc:  # noqa: BLE001 - 异常要变成模型可读的反馈
        return f"发送失败: {type(exc).__name__}: {exc}"

    if ok:
        target = "群" if chat_type == "group" else "联系人"
        return f"已发送给{target} {chat_id}"

    # 失败原因由发送器记录日志;这里给模型一个明确信号,让它决定是否重试或告知用户
    return f"发送失败: 无法送达 {chat_id}(请检查对方是否好友、机器人是否在线)"


# 打上"高危"标签: 该工具能以号主身份对外发声,群聊会话默认不授权(见 services/policy.py)。
# 新工具只要有类似影响,同样打这个标签即可被默认拦下。
send_qq_message.tags = [HIGH_RISK_TAG]


def create_message_tools() -> list[Any]:
    """返回消息类工具列表。"""
    return [send_qq_message]
