# =============================================================================
# scheduler.py - 定时唤醒调度器(时间驱动的心脏)
# -----------------------------------------------------------------------------
# 职责: 每秒扫一次 scheduled_events 表,把到期记录变成 timer 事件发给事件总线。
#
# 为什么需要它:
#   - 纯"消息驱动"只能处理"对方说了话才动"的场景;
#   - "等 10 分钟没回就催一下"这类任务需要"时间驱动":
#     wait 工具负责登记,这里负责到点唤醒;
#   - 事件进总线后,AgentGraph 收到 timer 事件,从 checkpoint 恢复会话继续跑图。
#
# 可靠性:
#   - 状态在 SQLite(pending/fired),服务重启后未到期的记录依然有效;
#   - 到期但发布失败的记录保持 pending,下个周期重试(至多重复一次)。
# =============================================================================

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from .events import Event, get_bus
from .services import schedules as schedules_service

# 轮询间隔(秒)。1 秒的粒度对"催一下"场景完全够用
POLL_INTERVAL_SECONDS = 1.0
# 每轮最多处理多少条到期记录(防积压雪崩,见 _tick 注释)
MAX_FIRED_PER_TICK = 10


class Scheduler:
    """定时唤醒调度器: 后台任务,到期发 timer 事件。"""

    def __init__(self) -> None:
        self._running = False
        self._task: asyncio.Task | None = None

    # ------------------------------------------------------------ 生命周期
    def start(self) -> None:
        """启动后台轮询任务(应用启动时调用一次)。"""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        """停止后台任务(应用退出时调用)。"""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    # ------------------------------------------------------------ 轮询
    async def _poll_loop(self) -> None:
        """每秒检查到期记录。到期的: 标记 fired → 发 timer 事件。"""
        while self._running:
            try:
                await self._tick()
            except Exception as exc:  # noqa: BLE001 - 轮询任务不能因单次异常退出
                print(f"[scheduler] 轮询异常: {type(exc).__name__}: {exc}")
            await asyncio.sleep(POLL_INTERVAL_SECONDS)

    async def _tick(self) -> None:
        """单次检查: 查到期 → 逐个标记并发布。

        每轮最多处理 MAX_FIRED_PER_TICK 条,防止"积压雪崩":
        一旦出现大量到期记录(例如事故残留、服务宕机期间积累),
        一次性全发会导致事件总线被几百次图执行塞满,新的定时唤醒
        反而排不上队(这正是线上出现过的问题)。
        """
        now = datetime.now(timezone.utc).isoformat()
        due_records = schedules_service.list_due(now, limit=MAX_FIRED_PER_TICK)
        if not due_records:
            return

        for record in due_records:
            # 先标记 fired,再发布事件:
            # 若发布瞬间进程崩溃,下个周期不会重复发(pending 已被消费)
            schedules_service.mark_fired(record["id"])
            # fire-and-forget: 事件交给事件循环后台处理,不阻塞本轮询。
            # 若在这里 await,图执行(一次 LLM 调用可能数十秒)会把
            # scheduler 卡死,导致其它到期任务无法及时触发。
            asyncio.create_task(self._publish_timer(record))

    async def _publish_timer(self, record: dict[str, Any]) -> None:
        """把一条到期记录转成 timer 事件发布(后台任务)。"""
        try:
            await get_bus().publish(
                Event(
                    event_type="timer",
                    platform="desktop",
                    chat_type="thread",
                    external_id=record["conversation_id"],
                    conversation_id=record["conversation_id"],
                    # 以会话内"系统提示"的方式注入: 告诉 agent 为什么被唤醒
                    text=record["note"] or "定时唤醒",
                )
            )
        except Exception as exc:  # noqa: BLE001 - 单个事件失败不能影响调度循环
            print(f"[scheduler] 发布 timer 事件失败: {type(exc).__name__}: {exc}")


# 模块级单例(与应用生命周期一致)
_scheduler: Scheduler | None = None


def get_scheduler() -> Scheduler:
    """返回全局调度器单例。"""
    global _scheduler
    if _scheduler is None:
        _scheduler = Scheduler()
    return _scheduler