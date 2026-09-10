# =============================================================================
# api/dispatch.py - 外部调度入口(主 agent / 其他系统派活)
# -----------------------------------------------------------------------------
# 定位: 让"别的 agent"能把活儿交给 Memo Echo(它是消息处理专门件)。
#
# 接口:
#   POST /api/dispatch           派发一个任务
#   GET  /api/dispatch/{id}      查任务状态与产物
#   GET  /api/dispatch           列任务(调试用)
#
# 四种任务类型(kind):
#   handle_message  把 content 当作"目标会话收到的消息",走完整 agent 流程
#                   (适合: 外部系统收到的消息转投递给本服务处理)
#   send_message    把 content 直接发到目标会话,不经 agent 决策
#                   (适合: 主 agent 自己决定了要说什么,只要执行发送)
#   task            把 instruction 变成该会话的目标(goal),agent 自主推进
#                   (适合: "帮我问小号几点上课然后转告 km"这类委托)
#   note            只记录上下文,不产生任何动作
#                   (适合: 给会话补充背景信息)
#
# 关键护栏:
#   · idempotency_key 幂等 —— 重复投递不重复执行(防"重试导致重复发消息");
#   · deadline 过期拒绝 —— 迟到的任务不执行;
#   · 会话拥堵返回 429 —— 调用方稍后重试;
#   · caller + context 全量留痕 —— 可追溯"谁让发的这条消息"。
# =============================================================================

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException

from ..agent.runtime import get_graph
from ..config import get_settings
from ..events import Event, EventKind, EventSource, get_bus
from .. import outbox
from ..services import conversations as conversations_service
from ..services import dispatches as dispatch_service

router = APIRouter(prefix="/api/dispatch", tags=["dispatch"])


# ---------------------------------------------------------------------------
# 鉴权
# ---------------------------------------------------------------------------
def _check_token(authorization: str | None = Header(default=None)) -> None:
    """Bearer 鉴权(与主 API 一致;未配置 token 时放行)。

    说明: 调度入口是"机器对机器"的接口,生产环境建议务必配置 api_token ——
    否则同机任何程序都能让机器人以你的身份发消息。
    """
    token = get_settings().api_token
    if not token:
        return
    if authorization != f"Bearer {token}":
        raise HTTPException(status_code=401, detail="unauthorized")


# ---------------------------------------------------------------------------
# 派发任务
# ---------------------------------------------------------------------------
@router.post("", dependencies=[Depends(_check_token)], status_code=202)
async def create_dispatch(body: dict[str, Any]) -> dict[str, Any]:
    """派发一个任务给 Memo Echo。

    请求体:
    ```json
    {
      "caller": "main-agent",
      "kind": "handle_message",              // handle_message|send_message|task|note
      "target": {"conversation_id": "..."}    // 或
              // {"platform":"qq","chat_type":"private","external_id":"1234"},
      "content": [{"type":"text","text":"..."}],  // handle_message/send_message 必填
      "instruction": "把这条转告 km",          // task 必填
      "context": {"upstream_task_id": "..."},  // 透传留痕
      "idempotency_key": "task-123",           // 强烈建议填(去重)
      "deadline": "2026-09-10T20:00:00+08:00", // 可选
      "result_mode": "poll",                   // poll|callback|none
      "callback_url": "http://127.0.0.1:9000/hooks"
    }
    ```

    返回 202 + 任务对象(含 task_id);重复投递时返回既有任务并带 duplicated=true。
    """
    kind = str(body.get("kind") or "").strip()
    if kind not in dispatch_service.ALL_KINDS:
        raise HTTPException(
            status_code=400,
            detail=f"kind 必须是 {sorted(dispatch_service.ALL_KINDS)} 之一",
        )

    # ---- 解析目标会话 ----
    conversation_id = _resolve_target(body)

    # ---- 内容校验(按类型区分必填项) ----
    text = _extract_text(body)
    if kind in (dispatch_service.KIND_HANDLE_MESSAGE, dispatch_service.KIND_SEND_MESSAGE) and not text:
        raise HTTPException(status_code=400, detail="该 kind 需要 content(至少一个 text 段)")
    if kind == dispatch_service.KIND_TASK and not str(body.get("instruction") or "").strip():
        raise HTTPException(status_code=400, detail="task 需要 instruction")

    # ---- 幂等: 先查重(避免重复执行 + 重复发送) ----
    idempotency_key = str(body.get("idempotency_key") or "").strip()
    if idempotency_key:
        existing = dispatch_service.create_dispatch(
            kind=kind,
            caller=str(body.get("caller") or ""),
            conversation_id=conversation_id,
            payload=body,
            idempotency_key=idempotency_key,
            deadline=str(body.get("deadline") or ""),
            result_mode=str(body.get("result_mode") or "none"),
            callback_url=str(body.get("callback_url") or ""),
        )
        if existing.get("duplicated"):
            # 重复投递: 直接返回既有任务(不重新执行)
            return existing

    # ---- 拥堵预检(会话已有排队时快速失败,让调用方稍后重试) ----
    graph = get_graph()
    if graph is not None and conversation_id and graph.is_busy(conversation_id):
        task = dispatch_service.create_dispatch(
            kind=kind,
            caller=str(body.get("caller") or ""),
            conversation_id=conversation_id,
            payload=body,
            idempotency_key="",  # 不占幂等键: 调用方重试时应能再次投递
            deadline=str(body.get("deadline") or ""),
            result_mode=str(body.get("result_mode") or "none"),
            callback_url=str(body.get("callback_url") or ""),
        )
        dispatch_service.mark_busy(task["task_id"])
        raise HTTPException(
            status_code=429,
            detail=f"目标会话繁忙,请稍后重试(task_id={task['task_id']})",
            headers={"Retry-After": "5"},
        )

    # ---- 创建任务 ----
    # 注: 幂等键在"未重复"时由这次创建写入;若调用方没给键,则每次都是新任务。
    task = dispatch_service.create_dispatch(
        kind=kind,
        caller=str(body.get("caller") or ""),
        conversation_id=conversation_id,
        payload=body,
        idempotency_key=idempotency_key,
        deadline=str(body.get("deadline") or ""),
        result_mode=str(body.get("result_mode") or "none"),
        callback_url=str(body.get("callback_url") or ""),
    )

    # ---- deadline 检查 ----
    if dispatch_service.is_expired(task):
        dispatch_service.mark_expired(task["task_id"])
        raise HTTPException(status_code=408, detail=f"任务已超过 deadline(task_id={task['task_id']})")

    # ---- 任务创建后按类型分发 ----
    # note / send_message 不需要 agent 决策,直接在这里完成并落结果;
    # handle_message / task 需要 agent 参与,转成事件发布给图执行。
    kind = task["kind"]
    caller = task.get("caller") or "external"
    context = {
        "task_id": task["task_id"],
        "caller": caller,
        "upstream": body.get("context") or {},
    }

    if kind == dispatch_service.KIND_NOTE:
        # 仅留痕: 记一条审计,不改动任何状态
        await get_bus().publish(
            Event.from_text(
                text or f"来自 {caller} 的备注",
                source=EventSource.AGENT,
                kind=EventKind.SYSTEM,
                platform="agent",
                chat_type="thread",
                external_id=conversation_id,
                conversation_id=conversation_id,
                should_respond=False,
                context=context,
            )
        )
        dispatch_service.mark_done(task["task_id"], {"note": "已记录"})
        return task

    if kind == dispatch_service.KIND_SEND_MESSAGE:
        # 直接发送: 调用方已经决定了要说什么,我们只负责"落库 + 发出"。
        # 刻意**不**走 agent —— 否则模型可能改写文案,违背调用方意图,
        # 而且要多花一次 LLM 调用(纯转发场景没必要)。
        result = await outbox.deliver(conversation_id, text, source="dispatch")
        if result["ok"]:
            dispatch_service.mark_done(
                task["task_id"],
                {"message_id": result["message_id"], "sent_text": text},
            )
        else:
            dispatch_service.mark_failed(task["task_id"], result["reason"])
        return task

    # handle_message / task: 转成事件交给 agent
    await _publish_agent_event(task, text=text, body=body, conversation_id=conversation_id, context=context)
    return task


# ---------------------------------------------------------------------------
# 查询任务
# ---------------------------------------------------------------------------
@router.get("/{task_id}", dependencies=[Depends(_check_token)])
def get_dispatch(task_id: str) -> dict[str, Any]:
    """查询任务状态与产物(轮询模式用这个)。"""
    task = dispatch_service.get_dispatch(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    task.pop("duplicated", None)
    return task


@router.get("", dependencies=[Depends(_check_token)])
def list_dispatches(status: str = "", caller: str = "", limit: int = 50) -> list[dict[str, Any]]:
    """列出调度任务(调试/审计用)。"""
    items = dispatch_service.list_dispatches(status=status, caller=caller, limit=limit)
    for item in items:
        item.pop("duplicated", None)
    return items


# ---------------------------------------------------------------------------
# 内部辅助
# ---------------------------------------------------------------------------
def _resolve_target(body: dict[str, Any]) -> str:
    """解析目标会话 ID。

    两种写法:
      {"target": {"conversation_id": "..."}}                          直接给 ID
      {"target": {"platform":"qq","chat_type":"private","external_id":"1234"}}  给三元组

    后者会查库/建会话,这样调用方不必先知道内部 ID。
    注意: 直接给 conversation_id 时也要**保底建号** —— 调度方给的很可能是
    一个尚未存在的会话 ID(比如主 agent 说"发到这个新会话"),不建号的话
    后续落库会被外键约束拒绝。
    """
    target = body.get("target") or {}
    if not isinstance(target, dict):
        raise HTTPException(status_code=400, detail="target 必须是对象")

    conversation_id = str(target.get("conversation_id") or "").strip()
    if conversation_id:
        conversations_service.ensure_conversation_by_id(conversation_id)
        return conversation_id

    platform = str(target.get("platform") or "").strip()
    chat_type = str(target.get("chat_type") or "private").strip()
    external_id = str(target.get("external_id") or "").strip()
    if platform and external_id:
        return conversations_service.ensure_conversation(platform, chat_type, external_id)

    raise HTTPException(
        status_code=400,
        detail="target 需要 conversation_id,或 platform + external_id",
    )


def _extract_text(body: dict[str, Any]) -> str:
    """从 content 里取出可读文本。

    支持两种写法:
      "content": [{"type":"text","text":"..."}]   标准内容段
      "text": "..."                                简写(纯文本任务)
    """
    # 简写优先
    direct = str(body.get("text") or "").strip()
    if direct:
        return direct

    content = body.get("content") or []
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""

    parts: list[str] = []
    for segment in content:
        if isinstance(segment, dict):
            if segment.get("type") == "text":
                parts.append(str(segment.get("text") or ""))
        elif isinstance(segment, str):
            parts.append(segment)
    return "".join(parts).strip()


async def _publish_agent_event(
    task: dict[str, Any],
    *,
    text: str,
    body: dict[str, Any],
    conversation_id: str,
    context: dict[str, Any],
) -> None:
    """把需要 agent 参与的任务转成事件发布。

    事件的两个分支:
      task（指令型）  → kind=instruction + command 非空
                        → agent_handler 先建 goal,再围绕目标推进
      handle_message  → kind=message(转投递的真实消息)
                        → 作为该会话收到的一条消息处理
    """
    kind = task["kind"]

    if kind == dispatch_service.KIND_TASK:
        instruction = str(body.get("instruction") or text or "").strip()
        event = Event.from_text(
            instruction,
            source=EventSource.AGENT,
            kind=EventKind.INSTRUCTION,
            platform="agent",
            chat_type="thread",
            external_id=conversation_id,
            conversation_id=conversation_id,
            should_respond=True,
            command=instruction,   # 非空 → agent_handler 会先建 goal
            context=context,
        )
    else:
        # handle_message: 外部系统把一条消息转投递进来,按正常消息处理
        event = Event.from_text(
            text,
            source=EventSource.AGENT,
            kind=EventKind.MESSAGE,
            platform="agent",
            chat_type="thread",
            external_id=conversation_id,
            conversation_id=conversation_id,
            sender_id=task.get("caller") or "",
            should_respond=True,
            context=context,
        )

    await get_bus().publish(event)
