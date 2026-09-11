# =============================================================================
# outbox.py - 出站消息投递(落库 + 发送)
# -----------------------------------------------------------------------------
# 为什么单独抽出来:
#   "把一条消息发给某个会话"是系统的基础动作,有三个触发来源:
#     ① agent 跑完图后回复(agent/nodes/finalize.py)
#     ② 外部调度要求直接发送(app/api/dispatch.py 的 send_message)
#     ③ 将来的通知/提醒类主动推送
#   三处都必须是**同一个动作序列**: 先写进对话历史(让下次推理知道"我说过"),
#   再走渠道发送(QQ/桌面 SSE)。分散实现迟早会漏掉其中一步 ——
#   典型后果: agent 不记得自己说过什么,于是重复发送同样的内容。
#
# 设计: 发送器(sender)由 main.py 组装时注入并登记到这里,
# 本模块只负责"按正确顺序做两件事",不关心具体渠道。
# =============================================================================

from __future__ import annotations

from typing import Any, Awaitable, Callable

from .agent.runtime import get_sender
from .services import conversations as conversations_service

# 发送器签名: (conversation_id, text, source) -> None
Sender = Callable[[str, str, str], Awaitable[None]]


async def deliver(
    conversation_id: str,
    text: str,
    *,
    source: str = "outbound",
    record_first: bool = True,
) -> dict[str, Any]:
    """把一条出站消息投递给会话: 先落库,再发送。

    参数:
      conversation_id: 目标会话
      text:            消息文本(非空才处理)
      source:          消息来源标记(默认 outbound;直接发送可传 "manual"/"dispatch")
      record_first:    是否"先落库,再发送"(默认,见下面的注意)。
                       传 False 表示**发成功才落库**,用于人确认后发送的场景 ——
                       发送失败时历史里不该出现一条"我说过"的记录,
                       否则下一轮模型会以为话已经带到了,而对方什么都没收到。

    返回: {"ok": bool, "message_id": str, "reason": str}
      ok=False 的典型原因: 会话不存在、文本为空、发送器未初始化。

    注意: 默认落库在发送**之前** —— 即使发送失败,历史里也有记录
    (调用方可通过 ok 字段判断是否需要重试);对"agent 自己决定要说的话"
    这是对的: 它确实说了,平台抖动不该让 agent 忘掉自己说过什么。
    """
    text = (text or "").strip()
    if not text:
        return {"ok": False, "message_id": "", "reason": "消息内容为空"}
    if not conversation_id:
        return {"ok": False, "message_id": "", "reason": "缺少会话 ID"}

    conversation = conversations_service.get_conversation(conversation_id)
    if not conversation:
        return {"ok": False, "message_id": "", "reason": f"会话不存在: {conversation_id}"}

    # ---- 1. 落库(assistant 身份,因为这是"我们说的话") ----
    message_id = _archive(conversation_id, text, source) if record_first else ""

    # ---- 2. 发送 ----
    sender = get_sender()
    if sender is None:
        return {"ok": False, "message_id": message_id, "reason": "发送器未初始化"}
    try:
        outcome = await sender(conversation_id, text, source)
    except Exception as exc:  # noqa: BLE001 - 发送失败要反馈给调用方,而不是抛出去打断流程
        return {"ok": False, "message_id": message_id, "reason": f"发送失败: {type(exc).__name__}: {exc}"}

    # ---- 3. 回填平台消息 ID ----
    # 有了它,平台把这条消息回显回来时才能认出来是"自己发的",不再重复入库。
    # (发送结果里没有 ID 也不影响流程 —— 只是那条回显会被当成新消息记一次)
    platform_message_id = ""
    if isinstance(outcome, dict):
        platform_message_id = str(outcome.get("platform_message_id") or "")
        if not outcome.get("ok", True):
            return {
                "ok": False,
                "message_id": message_id,
                "reason": str(outcome.get("error") or "发送失败"),
            }

    # ---- 4. record_first=False: 发送成功了才落库 ----
    # 放在这里而不是开头: 失败的发送不该在历史里留下"我说过"的痕迹。
    if not record_first:
        message_id = _archive(conversation_id, text, source)

    if platform_message_id and message_id:
        conversations_service.set_platform_message_id(message_id, platform_message_id)

    return {"ok": True, "message_id": message_id, "reason": ""}


def _archive(conversation_id: str, text: str, source: str) -> str:
    """把出站消息写进会话历史(assistant 身份),返回消息 ID。"""
    return conversations_service.add_message(
        conversation_id,
        {
            "id": None,          # 由 service 生成
            "role": "assistant",
            "content": text,
            "source": source,
            "created_at": "",    # 由 service 填充
            "goal_id": "",
        },
    )
