# =============================================================================
# api/routes.py - 桌面端 HTTP 接口
# -----------------------------------------------------------------------------
# 桌面端(前端)与上游主 agent 通过这里读写数据、下发命令。
# 接口设计尽量简单直接,每个接口对应一个明确动作:
#
#   GET   /api/conversations                会话列表(含策略)
#   POST  /api/conversations/resolve        按三元组定位/创建会话(配置前置步骤)
#   GET   /api/conversations/{id}           单个会话详情(含策略)
#   PATCH /api/conversations/{id}           改会话配置: 策略开关 + 人设/注意事项
#   GET   /api/conversations/{id}/messages  消息历史
#   POST  /api/conversations/{id}/messages  桌面端发一条消息(触发 agent)
#   POST  /api/conversations/{id}/goal      在会话上挂一个目标(命令)
#   GET   /api/conversations/{id}/goals     目标列表(进度卡)
#
# 鉴权: 本地使用,api_token 为空则放行;非空则要求 Authorization: Bearer <token>。
# =============================================================================

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException

from ..agent.runtime import get_graph
from ..config import get_settings
from ..events import Event, EventKind, EventSource, get_bus
from .. import memory as memory_layer
from ..services import conversations as conversations_service
from ..services import eventlog
from ..services import goals as goals_service
from ..services import policy as policy_service

router = APIRouter(prefix="/api")

# 会话行里可以改的非策略字段(会话档案)
PROFILE_FIELDS = ("title", "persona", "model_name")


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


def _conversation_view(conversation: dict[str, Any]) -> dict[str, Any]:
    """会话对外视图: 原始行 + 归一化后的 policy(前端/上游直接可用)。

    为什么要归一化: 库里存的是 monitor=0/1、alert_keywords='["急事"]' 这种原始形态,
    直接给前端会逼着每个调用方各写一遍解析。这里统一成 bool / 列表 / 字符串枚举。
    """
    view = dict(conversation)
    view["policy"] = policy_service.normalize_policy(conversation)
    return view


# ---------------------------------------------------------------------------
# 会话
# ---------------------------------------------------------------------------
@router.get("/conversations", dependencies=[Depends(_check_token)])
def list_conversations() -> list[dict[str, Any]]:
    """返回会话列表(按最近活跃排序),供左侧栏展示。每条都带归一化后的策略。"""
    return [_conversation_view(c) for c in conversations_service.list_conversations()]


@router.post("/conversations/resolve", dependencies=[Depends(_check_token)])
def resolve_conversation(body: dict[str, Any]) -> dict[str, Any]:
    """按平台三元组定位会话,不存在则创建,返回会话对象(含策略)。

    为什么需要这个接口:
      会话策略默认全关 —— 未开启监视的会话**不会有任何消息**,因此前端/主 agent
      在"配置一个还不存在的会话"时无从下手(没有 ID 可 PATCH)。
      本接口用 (platform, chat_type, external_id) 直接建行并返回 ID。

    请求体: {"platform":"qq","chat_type":"private","external_id":"2597164807","title":"小号"}
    """
    platform = str(body.get("platform") or "").strip()
    chat_type = str(body.get("chat_type") or "private").strip()
    external_id = str(body.get("external_id") or "").strip()
    if not platform or not external_id:
        raise HTTPException(status_code=400, detail="platform 与 external_id 必填")

    conversation_id = conversations_service.ensure_conversation(platform, chat_type, external_id)
    title = str(body.get("title") or "").strip()
    if title:
        conversations_service.update_profile(conversation_id, title=title)

    conversation = conversations_service.get_conversation(conversation_id) or {}
    return _conversation_view(conversation)


@router.get("/conversations/{conversation_id}", dependencies=[Depends(_check_token)])
def get_conversation(conversation_id: str) -> dict[str, Any]:
    """读取单个会话详情(含归一化策略)。"""
    conversation = conversations_service.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return _conversation_view(conversation)


@router.patch("/conversations/{conversation_id}", dependencies=[Depends(_check_token)])
def update_conversation(conversation_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """修改会话配置: 值守策略 + 会话档案(人设/注意事项)。

    请求体(全可选,只传要改的):
    ```json
    {
      "monitor": true,                    // 监视开关(总开关)
      "reply_mode": "auto",               // off | draft | auto
      "alert_enabled": true,              // 重要消息上报
      "alert_keywords": ["急事","改时间"],
      "require_human_confirmation": true, // 拿不准必须请示
      "digest_window_seconds": 1800,      // 攒批窗口
      "digest_max_messages": 20,          // 攒批条数
      "allowed_tools": ["send_qq_message"],// 工具授权(空=按会话类型默认)
      "persona": "注意别答应晚上十点后的活动"   // 注意事项/人设
    }
    ```

    返回: {"conversation": 会话视图, "changed": {字段: [旧值,新值]}, "implied": [自动打开的开关]}
    """
    if not body:
        raise HTTPException(status_code=400, detail="请求体不能为空")

    conversation = conversations_service.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="会话不存在")

    policy_changes = {key: value for key, value in body.items() if key in policy_service.POLICY_FIELDS}
    profile_changes = {key: value for key, value in body.items() if key in PROFILE_FIELDS}
    unknown = set(body) - set(policy_changes) - set(profile_changes)
    if unknown:
        raise HTTPException(status_code=400, detail=f"不支持的字段: {sorted(unknown)}")

    changed: dict[str, Any] = {}
    implied: list[str] = []
    if policy_changes:
        try:
            result = policy_service.update_policy(conversation_id, **policy_changes)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        changed.update(result["changed"])
        implied = result["implied"]
    if profile_changes:
        changed.update(conversations_service.update_profile(conversation_id, **profile_changes))

    updated = conversations_service.get_conversation(conversation_id) or {}
    return {"conversation": _conversation_view(updated), "changed": changed, "implied": implied}



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


# ---------------------------------------------------------------------------
# 记忆健康度(排障用)
# ---------------------------------------------------------------------------
@router.get("/memory/health", dependencies=[Depends(_check_token)])
def memory_health() -> dict[str, Any]:
    """长期记忆的健康状况 —— 专门用来发现"静默的记忆缺失"。

    为什么需要这个接口: 攒批总结是长期记忆的**唯一入口**,而它的失效方式是
    完全静默的 —— 模型输出解析不了时,业务上等同于"这批没值得记的",
    水位线照常推进,那批消息再也不会被总结。没有这个接口,只能翻服务日志。

    返回:
      enabled          记忆功能是否启用
      init_error       Doppel 初始化失败原因(空=正常)
      summary          总结器统计(调用/空结果/解析失败次数 + 失败样本)
      batches          各会话的攒批进度(待处理条数、上次状态与错误)
    """
    from .. import batches as batches_module

    batches = [
        {
            "conversation_id": row["conversation_id"],
            "pending_count": row["pending_count"],
            "last_status": row["last_status"],
            "last_error": row["last_error"],
            "last_run_at": row["last_run_at"],
        }
        for row in batches_module.list_progress(limit=50)
    ]
    return {
        "enabled": memory_layer.is_enabled(),
        "init_error": memory_layer.init_error(),
        "summary": batches_module.summary_health(),
        "batches": batches,
    }

