# =============================================================================
# api/routes.py - 桌面端 HTTP 接口
# -----------------------------------------------------------------------------
# 桌面端(前端)通过这里读写数据、下发命令。
# 接口设计尽量简单直接,每个接口对应一个明确动作:
#
#   GET  /api/conversations                会话列表
#   GET  /api/conversations/{id}/messages  消息历史
#   POST /api/conversations/{id}/messages  桌面端发一条消息(触发 agent)
#   POST /api/conversations/{id}/goal      在会话上挂一个目标(命令)
#   GET  /api/conversations/{id}/goals     目标列表(进度卡)
#
# 鉴权: 本地使用,api_token 为空则放行;非空则要求 Authorization: Bearer <token>。
# =============================================================================

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException

from ..agent.graph import ConversationBusyError
from ..agent.runtime import get_graph
from ..config import get_settings
from ..events import Event, EventKind, EventSource, get_bus
from ..services import conversations as conversations_service
from ..services import eventlog
from ..services import goals as goals_service

router = APIRouter(prefix="/api")


# ---------------------------------------------------------------------------
# 鉴权依赖(可选)
# ---------------------------------------------------------------------------
def _check_token(authorization: str | None = Header(default=None)) -> None:
    """简单 Bearer 鉴权;未配置 token 时全部放行。"""
    token = get_settings().api_token
    if not token:
        return
    if authorization != f"Bearer {token}":
        raise HTTPException(status_code=401, detail="unauthorized")


# ---------------------------------------------------------------------------
# 会话
# ---------------------------------------------------------------------------
@router.get("/conversations", dependencies=[Depends(_check_token)])
def list_conversations() -> list[dict[str, Any]]:
    """返回会话列表(按最近活跃排序),供左侧栏展示。"""
    return conversations_service.list_conversations()


@router.get("/conversations/{conversation_id}/messages", dependencies=[Depends(_check_token)])
def list_messages(conversation_id: str) -> list[dict[str, Any]]:
    """返回会话消息历史(时间正序)。"""
    return conversations_service.list_messages(conversation_id)


# ---------------------------------------------------------------------------
# 发消息 / 下命令
# ---------------------------------------------------------------------------
@router.post("/conversations/{conversation_id}/messages", dependencies=[Depends(_check_token)])
async def post_message(conversation_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """桌面端用户发言: 写入消息 → 发布事件 → agent 处理。

    请求体: {"text": "..."}
    返回: {"conversation_id": ..., "event_id": ...}(回复经 SSE 推送,不在此等待)

    异常: 会话拥堵时返回 429(排队事件超限),稍后重试即可。
    """
    text = str(body.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")

    # 会话拥堵预检: 已达排队上限就直接回 429,让调用方稍后重试 ——
    # 比"事件被静默丢弃、用户看不到回复"要好得多。
    graph = get_graph()
    if graph is not None and graph.is_busy(conversation_id):
        raise HTTPException(
            status_code=429,
            detail="会话繁忙,请稍后重试",
            headers={"Retry-After": "5"},
        )

    # 组装事件(platform=desktop, chat_type=thread)。
    # 注意: 桌面端消息直接携带 conversation_id(API 路径里的 ID),
    # 避免 graph.run_event 走 ensure_conversation 另建一个随机 uuid 的新会话。
    event = Event.from_text(
        text,
        source=EventSource.DESKTOP,
        kind=EventKind.MESSAGE,
        platform="desktop",
        chat_type="thread",
        external_id=conversation_id,
        conversation_id=conversation_id,
        sender_id="desktop-user",
    )

    # 发布到事件总线(审计处理器 + agent 处理器已注册)
    await get_bus().publish(event)

    return {"conversation_id": conversation_id, "event_id": event.event_id}


@router.post("/conversations/{conversation_id}/goal", dependencies=[Depends(_check_token)])
def create_goal(conversation_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """在会话上创建目标(把命令变成"带目标的对话")。

    请求体: {"objective": "问km今晚几点上课,转告小号"}
    返回: 新目标对象。
    """
    objective = str(body.get("objective") or "").strip()
    if not objective:
        raise HTTPException(status_code=400, detail="objective is required")

    # 保底: goals 表对 conversations 有外键约束,
    # 若该会话还没落库(桌面端首次操作就是挂目标),直接插入会失败。
    conversations_service.ensure_conversation_by_id(conversation_id)
    return goals_service.create_goal(conversation_id, objective)


@router.get("/conversations/{conversation_id}/goals", dependencies=[Depends(_check_token)])
def list_goals(conversation_id: str) -> list[dict[str, Any]]:
    """返回会话的目标列表(进度卡数据)。"""
    return goals_service.list_goals(conversation_id)


# ---------------------------------------------------------------------------
# 事件审计(排障用)
# ---------------------------------------------------------------------------
@router.get("/events", dependencies=[Depends(_check_token)])
def list_events(
    conversation_id: str = "",
    kind: str = "",
    limit: int = 100,
) -> list[dict[str, Any]]:
    """查询事件审计日志(时间倒序)。

    用途: 排查"消息到底收到没有/解析成什么了/为什么没回"。
    参数:
      conversation_id: 只看某会话(可选)
      kind:            只看某类事件(可选,如 notice/request/message)
      limit:           返回条数(默认 100)
    """
    return eventlog.list_events(conversation_id, limit=limit, kind=kind)


@router.get("/events/stats", dependencies=[Depends(_check_token)])
def event_stats() -> dict[str, int]:
    """按事件类别统计条数(快速看系统都在处理什么)。"""
    return eventlog.count_by_kind()
