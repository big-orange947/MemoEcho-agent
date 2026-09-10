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

from ..agent.graph import AgentGraph
from ..config import get_settings
from ..events import Event, get_bus
from ..services import conversations as conversations_service
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
    """
    text = str(body.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")

    # 组装事件(platform=desktop, chat_type=thread)
    # 注意: 桌面端消息直接携带 conversation_id(API 路径里的 ID),
    # 避免 graph.run_event 走 ensure_conversation 另建一个随机 uuid 的新会话。
    event = Event(
        event_type="message",
        platform="desktop",
        chat_type="thread",
        external_id=conversation_id,
        conversation_id=conversation_id,
        text=text,
        sender_id="desktop-user",
    )

    # 发布到事件总线(AgentGraph 已注册为处理器)
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
    return goals_service.create_goal(conversation_id, objective)


@router.get("/conversations/{conversation_id}/goals", dependencies=[Depends(_check_token)])
def list_goals(conversation_id: str) -> list[dict[str, Any]]:
    """返回会话的目标列表(进度卡数据)。"""
    return goals_service.list_goals(conversation_id)
