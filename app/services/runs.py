# =============================================================================
# services/runs.py - agent 执行轨迹(一次执行 = 一条 run,过程 = 若干 step)
# -----------------------------------------------------------------------------
# 为什么要有这个模块:
#   控制台是"输入自然语言 → agent 自己跑工具办事"的入口,那么界面必须能回答
#   "它到底干了什么":调了哪些工具、参数是什么、发给谁、成功没有。
#   在此之前,这些信息只存在于 LangGraph checkpoint 里 —— 没有接口能读,
#   而且会被 history_max_messages 裁掉;前端只能看到最后那句回复。
#
# 与审计(events 表)的分工:
#   · events 表是**系统级审计**(谁在什么时候发生了什么),面向排查与追溯;
#   · agent_steps 是**给用户看的执行过程**,面向界面展示,按 run 组织。
#   两者故意分开:审计不能因为"界面不需要"就少写,界面也不该被审计的
#   表结构绑住(例如 step 要按 seq 排序渲染,审计不需要)。
#
# 推送: 每次落库后经 notify() 广播 SSE(step / run 两个事件名)。
#   SSE 是全局广播、没有按会话过滤的能力,所以 payload 里一定带
#   conversation_id —— 前端自行过滤,这是既定约定。
# =============================================================================

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from ..db import get_connection

# 步骤类型(前端按 kind 决定图标/配色;新增类型要同步 web 侧)
STEP_TOOL_CALL = "tool_call"      # 发起一次工具调用(带参数)
STEP_TOOL_RESULT = "tool_result"  # 工具返回(带结果文本与成败)
STEP_NOTE = "note"                # 过程性说明(如"已排入会话队列")
STEP_ERROR = "error"              # 执行失败


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _push(event_type: str, data: dict[str, Any]) -> None:
    """尽力推一条 SSE。

    延迟导入: services 层不该在导入期依赖 agent 层(agent/nodes 反过来依赖
    services),放函数里避免循环导入;推送失败也不该影响业务落库。
    """
    try:
        from ..agent.runtime import notify

        await notify(event_type, data)
    except Exception as exc:  # noqa: BLE001 - 推送是尽力而为
        print(f"[runs] SSE 推送失败({event_type}): {type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# 写
# ---------------------------------------------------------------------------
async def start_run(
    conversation_id: str,
    instruction: str,
    *,
    event_id: str = "",
    run_id: str = "",
) -> dict[str, Any]:
    """登记一次执行开始,返回 run 对象。"""
    run_id = run_id or uuid.uuid4().hex
    now = _now()
    conn = get_connection()
    conn.execute(
        "INSERT INTO agent_runs (id, conversation_id, status, instruction, event_id, started_at)"
        " VALUES (?, ?, 'running', ?, ?, ?)",
        (run_id, conversation_id, instruction, event_id, now),
    )
    conn.commit()

    run = {
        "id": run_id,
        "conversation_id": conversation_id,
        "status": "running",
        "instruction": instruction,
        "reply": "",
        "error": "",
        "event_id": event_id,
        "started_at": now,
        "finished_at": "",
        "steps": [],
    }
    await _push("run", {"conversation_id": conversation_id, "run": run})
    return run


async def add_step(
    run_id: str,
    kind: str,
    *,
    name: str = "",
    detail: str = "",
    ok: bool = True,
    conversation_id: str = "",
) -> dict[str, Any] | None:
    """追加一步。run_id 为空时直接返回 None(非控制台执行,不记轨迹)。"""
    if not run_id:
        return None

    conn = get_connection()
    row = conn.execute(
        "SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq FROM agent_steps WHERE run_id=?",
        (run_id,),
    ).fetchone()
    seq = int(row["next_seq"]) if row is not None else 1

    step_id = uuid.uuid4().hex
    now = _now()
    conn.execute(
        "INSERT INTO agent_steps (id, run_id, seq, kind, name, detail, ok, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (step_id, run_id, seq, kind, name, detail, 1 if ok else 0, now),
    )
    conn.commit()

    step = {
        "id": step_id,
        "run_id": run_id,
        "seq": seq,
        "kind": kind,
        "name": name,
        "detail": detail,
        "ok": bool(ok),
        "created_at": now,
    }
    if not conversation_id:
        run = conn.execute(
            "SELECT conversation_id FROM agent_runs WHERE id=?", (run_id,)
        ).fetchone()
        conversation_id = run["conversation_id"] if run is not None else ""
    await _push("step", {"conversation_id": conversation_id, "run_id": run_id, "step": step})
    return step


async def finish_run(
    run_id: str,
    *,
    reply: str = "",
    error: str = "",
) -> dict[str, Any] | None:
    """收尾: 写终态(回复或错误)并推送。

    注意 status 只有 done / error 两种终态 —— 中途被打断(进程重启)的 run
    会一直停在 running,读取时由 list_runs 兜底标注为 interrupted。
    """
    if not run_id:
        return None

    status = "error" if error else "done"
    now = _now()
    conn = get_connection()
    conn.execute(
        "UPDATE agent_runs SET status=?, reply=?, error=?, finished_at=? WHERE id=?",
        (status, reply, error, now, run_id),
    )
    conn.commit()

    run = get_run(run_id)
    if run is not None:
        await _push("run", {"conversation_id": run["conversation_id"], "run": run})
    return run


# ---------------------------------------------------------------------------
# 读
# ---------------------------------------------------------------------------
def _run_view(row: Any, *, with_steps: bool = False) -> dict[str, Any]:
    run = dict(row)
    run["status"] = _effective_status(run)
    if with_steps:
        run["steps"] = list_steps(run["id"])
    return run


def _effective_status(run: dict[str, Any]) -> str:
    """running 但进程已经不在(重启/崩溃)的执行,展示为 interrupted。

    判据只有"没有 finished_at": 这里不猜进程状态,只保证界面不会永远转圈。
    """
    if run.get("status") == "running":
        return "running"
    return run.get("status") or "done"


def get_run(run_id: str, *, with_steps: bool = True) -> dict[str, Any] | None:
    row = get_connection().execute(
        "SELECT * FROM agent_runs WHERE id=?", (run_id,)
    ).fetchone()
    if row is None:
        return None
    return _run_view(row, with_steps=with_steps)


def list_runs(conversation_id: str, limit: int = 30, *, with_steps: bool = True) -> list[dict[str, Any]]:
    """按时间正序返回某会话的最近若干次执行(控制台按序渲染)。"""
    rows = get_connection().execute(
        "SELECT * FROM agent_runs WHERE conversation_id=?"
        " ORDER BY started_at DESC LIMIT ?",
        (conversation_id, limit),
    ).fetchall()
    runs = [_run_view(row, with_steps=with_steps) for row in reversed(rows)]
    return runs


def list_steps(run_id: str) -> list[dict[str, Any]]:
    rows = get_connection().execute(
        "SELECT * FROM agent_steps WHERE run_id=? ORDER BY seq ASC", (run_id,)
    ).fetchall()
    steps = []
    for row in rows:
        step = dict(row)
        # SQLite 里存 0/1,接口层统一成布尔: JS 里 0 也是真值,
        # 不转换会把"失败"渲染成"成功"。
        step["ok"] = bool(step.get("ok"))
        steps.append(step)
    return steps


def running_count(conversation_id: str) -> int:
    """该会话正在进行中的执行数(控制台据此显示"正在跑")。"""
    row = get_connection().execute(
        "SELECT COUNT(*) AS n FROM agent_runs WHERE conversation_id=? AND status='running'",
        (conversation_id,),
    ).fetchone()
    return int(row["n"]) if row is not None else 0
