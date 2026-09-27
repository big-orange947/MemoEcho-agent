# =============================================================================
# api/console.py - 控制台: 对话线程 + 自然语言执行入口
# -----------------------------------------------------------------------------
# 这一组接口回答的问题是"我让它办的事,它跑成什么样了"。
#
# 与 api/routes.py 里那批"会话"接口的区别:
#   · 会话(conversations) = 平台侧的对话对象(QQ 好友/群),用于值守;
#   · 线程(threads)       = 你与 agent 之间的对话容器,用于下指令。
#   两者在库里是同一张表(线程就是 platform=desktop/chat_type=thread 的会话),
#   但语义与入口完全分开 —— 这正是 docs/workspace-chat-console.md 的
#   "Thread 是对话,Task 是执行"。
#
# 为什么发一条指令要 202 + 后台跑:
#   一次执行里可能有多次模型调用与工具调用(问人、等人回、再转告),耗时以分钟计。
#   同步接口会让浏览器一直挂着,失败还被吞成 200。改成: 立刻返回 run_id,
#   过程经 SSE(step/run)推给前端,断线后可用 GET /api/runs/{id} 补全。
# =============================================================================

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from ..agent.runtime import get_graph
from ..db import get_connection
from ..events import Event, EventKind, EventSource
from ..services import conversations as conversations_service
from ..services import goals as goals_service
from ..services import runs as runs_service
from .routes import _check_token

router = APIRouter(prefix="/api")

# 后台执行任务的强引用: asyncio 只持弱引用,不留着会被 GC 掉(跑到一半消失)
_background_tasks: set[asyncio.Task] = set()

# 线程 = platform=desktop / chat_type=thread 的会话行
_THREAD_PLATFORM = "desktop"
_THREAD_CHAT_TYPE = "thread"


def _now() -> str:
    """与 services 层一致的时间格式(UTC ISO)。"""
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# 线程
# ---------------------------------------------------------------------------
def _thread_view(row: Any) -> dict[str, Any]:
    thread = dict(row)
    conversation_id = thread["id"]
    return {
        "id": conversation_id,
        "title": thread.get("title") or "",
        "created_at": thread.get("created_at") or "",
        "updated_at": thread.get("updated_at") or "",
        "archived": bool(thread.get("archived")),
        "running": runs_service.running_count(conversation_id),
    }


def _get_thread_or_404(thread_id: str) -> dict[str, Any]:
    conversation = conversations_service.get_conversation(thread_id)
    if conversation is None or conversation.get("platform") != _THREAD_PLATFORM:
        raise HTTPException(status_code=404, detail="对话不存在")
    return conversation


@router.get("/threads", dependencies=[Depends(_check_token)])
def list_threads(include_archived: bool = False, limit: int = Query(50, ge=1, le=200)) -> list[dict[str, Any]]:
    """列出控制台对话(最近活跃在前)。

    默认不含归档 —— 归档是"收进抽屉",要看得显式要(include_archived=true)。
    """
    sql = (
        "SELECT * FROM conversations WHERE platform=? AND chat_type=?"
        + ("" if include_archived else " AND COALESCE(archived, 0)=0")
        + " ORDER BY updated_at DESC LIMIT ?"
    )
    rows = get_connection().execute(
        sql, (_THREAD_PLATFORM, _THREAD_CHAT_TYPE, limit)
    ).fetchall()
    return [_thread_view(row) for row in rows]


@router.post("/threads", dependencies=[Depends(_check_token)], status_code=201)
def create_thread(body: dict[str, Any] | None = None) -> dict[str, Any]:
    """新建一个空对话(标题可选)。

    线程 ID 就是会话 ID,并同时作为 external_id —— 与桌面端消息链路一致
    (见 services/conversations.ensure_conversation_by_id 的约定)。
    """
    title = str((body or {}).get("title") or "").strip()
    thread_id = uuid.uuid4().hex
    conversations_service.ensure_conversation_by_id(thread_id)
    if title:
        conversations_service.update_profile(thread_id, title=title)
    conversation = conversations_service.get_conversation(thread_id) or {}
    return _thread_view(conversation)


@router.patch("/threads/{thread_id}", dependencies=[Depends(_check_token)])
def update_thread(thread_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """重命名 / 归档 / 取消归档。"""
    _get_thread_or_404(thread_id)
    changes: dict[str, Any] = {}
    if "title" in body:
        changes["title"] = str(body.get("title") or "").strip()
    if "archived" in body:
        archived = 1 if body.get("archived") else 0
        conn = get_connection()
        conn.execute(
            "UPDATE conversations SET archived=?, updated_at=? WHERE id=?",
            (archived, _now(), thread_id),
        )
        conn.commit()
    if changes:
        conversations_service.update_profile(thread_id, **changes)
    conversation = conversations_service.get_conversation(thread_id) or {}
    return _thread_view(conversation)


@router.delete("/threads/{thread_id}", dependencies=[Depends(_check_token)])
def delete_thread(thread_id: str) -> dict[str, Any]:
    """删除对话及其消息/执行记录(外键级联)。

    只允许删线程: 这是用户自己开的对话,删了不影响任何 QQ 会话。
    """
    _get_thread_or_404(thread_id)
    conn = get_connection()
    conn.execute("DELETE FROM conversations WHERE id=?", (thread_id,))
    conn.commit()
    return {"deleted": thread_id}


# ---------------------------------------------------------------------------
# 消息与执行轨迹
# ---------------------------------------------------------------------------
@router.get("/threads/{thread_id}/messages", dependencies=[Depends(_check_token)])
def list_thread_messages(thread_id: str, limit: int = Query(200, ge=1, le=500)) -> list[dict[str, Any]]:
    """线程的消息流(用户指令 + agent 回复,时间正序)。"""
    _get_thread_or_404(thread_id)
    return conversations_service.list_messages(thread_id, limit)


@router.get("/threads/{thread_id}/runs", dependencies=[Depends(_check_token)])
def list_thread_runs(thread_id: str, limit: int = Query(30, ge=1, le=100)) -> list[dict[str, Any]]:
    """线程的执行轨迹(含每一步工具调用/结果),用于刷新后重建界面。"""
    _get_thread_or_404(thread_id)
    return runs_service.list_runs(thread_id, limit)


@router.get("/runs/{run_id}", dependencies=[Depends(_check_token)])
def get_run(run_id: str) -> dict[str, Any]:
    """单次执行的完整轨迹(SSE 断线后的兜底读取)。"""
    run = runs_service.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="执行记录不存在")
    return run


@router.post("/threads/{thread_id}/messages", dependencies=[Depends(_check_token)], status_code=202)
async def post_thread_message(thread_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """发一条自然语言指令给 agent(立即返回,执行过程走 SSE)。

    请求体: {"text": "帮我问一下 km 今晚有没有空打游戏"}
    返回: {"thread_id": ..., "run_id": ..., "event_id": ..., "goal_id": ...}

    语义(与 docs/workspace-chat-console.md 的"命令"一致):
      1. 目标: 把指令变成"带目标的对话",agent 才能跨轮推进
         ("问 km → 等回复 → 转告小号"这种要等对方回话的事,没有目标就会断);
      2. 发布 desktop/thread 事件 —— desktop 属于"显式指令",不受值守策略门禁;
      3. 后台跑图,过程中每一步写进 agent_steps 并推 SSE。
    """
    text = str(body.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    _get_thread_or_404(thread_id)

    # 同一条线程串行执行: agent 的上下文是这条线程的历史,并行跑会互相踩
    graph = get_graph()
    if graph is not None and graph.is_busy(thread_id):
        raise HTTPException(
            status_code=429,
            detail="上一条还在跑,等它结束再发",
            headers={"Retry-After": "5"},
        )

    # 1. 用户消息落库(控制台自己的消息流;与 agent 的回复同表,靠 role 区分)
    # 1. 用户消息由图的 ingest 节点落库(入站消息的唯一入口)—— 这里**不要**
    #    自己再写一遍,否则线程里会出现两条一模一样的指令。
    conversations_service.ensure_conversation_by_id(thread_id)

    # 2. 目标: 指令即目标。完成/放弃由 agent 自己判断(reflect/finalize 维护)。
    goal = goals_service.create_goal(thread_id, text)

    # 3. 事件: kind=INSTRUCTION + command 让它带"命令"语义(agent_handler 见到
    #    command 会建目标,这里已建,故用 MESSAGE 语义 + 显式目标已存在的路径)。
    event = Event.from_text(
        text,
        source=EventSource.DESKTOP,
        kind=EventKind.MESSAGE,
        platform="desktop",
        chat_type="thread",
        external_id=thread_id,
        conversation_id=thread_id,
        sender_id="desktop-user",
    )

    # 4. 执行记录: run_id 通过 event.context 透传进图(节点埋点靠它归集)
    run = await runs_service.start_run(thread_id, text, event_id=event.event_id)
    event.context["run_id"] = run["id"]

    task = asyncio.create_task(_run_in_background(thread_id, event, run["id"]))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    return {
        "thread_id": thread_id,
        "run_id": run["id"],
        "event_id": event.event_id,
        "goal_id": goal.get("id", ""),
    }


async def _run_in_background(thread_id: str, event: Event, run_id: str) -> None:
    """后台执行一次指令,并把终态写回 run。

    为什么不用 EventBus: 总线的 publish 是"广播给所有处理器",而这里要的是
    "跑完告诉我结果" —— 且总线的异常是被吞掉的(见 main.agent_handler),
    控制台需要拿到错误原文展示给用户。
    """
    from ..agent.runtime import get_graph as _get_graph

    graph = _get_graph()
    if graph is None:
        await runs_service.finish_run(run_id, error="agent 未初始化")
        return
    try:
        reply = await graph.run_event(event.to_payload())
        await runs_service.finish_run(run_id, reply=reply or "")
    except Exception as exc:  # noqa: BLE001 - 失败要变成用户看得见的终态
        print(f"[console] 执行失败 run={run_id}: {type(exc).__name__}: {exc}")
        await runs_service.add_step(
            run_id,
            runs_service.STEP_ERROR,
            name=type(exc).__name__,
            detail=str(exc),
            ok=False,
            conversation_id=thread_id,
        )
        await runs_service.finish_run(run_id, error=f"{type(exc).__name__}: {exc}")
