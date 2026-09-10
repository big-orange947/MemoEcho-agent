# =============================================================================
# tools/wait.py - 等待/定时唤醒工具
# -----------------------------------------------------------------------------
# 让 LLM 可以"等一会儿再继续",例如:
#   "等小号 10 分钟,没回就催一下" → wait(seconds=600, note="催小号回复")
#
# 为什么不是 sleep:
#   - agent 跑在单进程事件循环里,阻塞 sleep 会卡住整个服务;
#   - 正确语义是"登记一条定时唤醒": wait 工具只写数据库,立即返回,
#     后台 Scheduler 到点发 timer 事件,该会话从 checkpoint 恢复继续跑图。
#
# conversation_id 来源: LangGraph 工具调用时自动注入 RunnableConfig,
# config["configurable"]["thread_id"] 就是当前会话 ID。
# =============================================================================

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from ..services import schedules as schedules_service


@tool
def wait(seconds: int, config: RunnableConfig, note: str = "") -> str:
    """设置等待: 在 seconds 秒后自动唤醒本会话继续当前任务。

    seconds: 要等待的秒数(正整数)。例如等 10 分钟写 600。
    note: 等待原因 / 到期后要做什么(例如 "催小号回复"),会作为唤醒提示。
    返回: 登记结果描述。注意: 这不是 sleep —— 调用后本轮回合结束,
    到点后本会话会被自动唤醒,你可以继续之前的任务。
    """
    if seconds <= 0:
        return "错误: seconds 必须为正整数"

    # 从 LangGraph 注入的 config 中取当前会话 ID(thread_id)
    configurable = config.get("configurable") or {}
    conversation_id = configurable.get("thread_id") or ""
    if not conversation_id:
        return "错误: 无法确定当前会话,等待已取消"

    due_at = (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()
    schedules_service.create_schedule(conversation_id, due_at, note or "定时唤醒")

    return f"已登记: 等待 {seconds} 秒后自动唤醒本会话继续处理(note={note or '定时唤醒'})"


def create_wait_tools() -> list[object]:
    """返回等待类工具列表。"""
    return [wait]