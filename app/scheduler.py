# =============================================================================
# scheduler.py - 定时唤醒调度器(时间驱动的心脏)
# -----------------------------------------------------------------------------
# 职责(两条,频率差两个数量级):
#   1. 每秒扫一次 scheduled_events 表,把到期记录变成 timer 事件发给事件总线;
#   2. 每 BATCH_SCAN_INTERVAL_SECONDS(60 秒)扫一次"攒批记忆"
#      (见 app/batches.py),把该总结的会话各总结一轮。
#
# 为什么需要它:
#   - 纯"消息驱动"只能处理"对方说了话才动"的场景;
#   - "等 10 分钟没回就催一下"这类任务需要"时间驱动":
#     wait 工具负责登记,这里负责到点唤醒;
#   - 事件进总线后,AgentGraph 收到 timer 事件,从 checkpoint 恢复会话继续跑图。
#   - 长期记忆需要"攒一批再总结",而"攒够了/对方不说话了"这两个条件
#     都只能由时间驱动的定时扫描发现(消息路径不知道"以后还会不会来消息")。
#
# 可靠性:
#   - 状态在 SQLite(pending/fired),服务重启后未到期的记录依然有效;
#   - 到期但发布失败的记录保持 pending,下个周期重试(至多重复一次);
#   - 攒批扫描低频 + 单轮只允许一个在跑(见 _scan_batches 注释)。
# =============================================================================

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from . import batches
from . import consolidation
from .config import get_settings
from .events import Event, EventKind, EventSource, get_bus
from .services import schedules as schedules_service

# 轮询间隔(秒)。1 秒的粒度对"催一下"场景完全够用
POLL_INTERVAL_SECONDS = 1.0
# 每轮最多处理多少条到期记录(防积压雪崩,见 _tick 注释)
MAX_FIRED_PER_TICK = 10
# 攒批记忆扫描间隔(秒)。刻意比定时唤醒慢两个数量级:
#   · 单次扫描会调 LLM 总结,成本高;
#   · 触发条件是"攒够 N 条"或"静止半小时",秒级精度毫无意义;
#   · 每秒扫库(还带 COUNT/MAX 查询)纯属浪费。
BATCH_SCAN_INTERVAL_SECONDS = 60.0


class Scheduler:
    """定时唤醒调度器: 后台任务,到期发 timer 事件 + 定期攒批记忆 + 定期清理存储。"""

    def __init__(self) -> None:
        self._running = False
        self._task: asyncio.Task | None = None
        # 上次攒批扫描的时刻(单调时钟;0 = 还没扫过,启动后第一轮就补扫一次)
        self._last_batch_scan = 0.0
        # 正在跑的攒批任务(一次只允许一个,见 _scan_batches)
        self._batch_task: asyncio.Task | None = None
        # 上次存储清理的时刻(单调时钟;0 = 还没跑过,启动后第一轮就补跑一次)
        self._last_retention_run = 0.0
        self._retention_task: asyncio.Task | None = None

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
        if self._retention_task is not None and not self._retention_task.done():
            # 清理任务可能正卡在 checkpoint 精简上;取消它 ——
            # 删除是分批提交的,已删的不会回滚,未删的下次继续
            self._retention_task.cancel()
            try:
                await self._retention_task
            except asyncio.CancelledError:
                pass
            except Exception as exc:  # noqa: BLE001 - 退出时的清理失败无需打扰用户
                print(f"[scheduler] 存储清理任务退出异常: {type(exc).__name__}: {exc}")
            self._retention_task = None
        if self._batch_task is not None and not self._batch_task.done():
            # 攒批任务可能正卡在 LLM 调用上,取消它 —— 它没有不可中断的副作用
            # (写入只在整轮结束时由 Doppel 提交,检查点没提交就下次重来)。
            self._batch_task.cancel()
            try:
                await self._batch_task
            except asyncio.CancelledError:
                pass
            except Exception as exc:  # noqa: BLE001 - 退出时的清理失败无需打扰用户
                print(f"[scheduler] 攒批任务退出异常: {type(exc).__name__}: {exc}")
            self._batch_task = None
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
                await self._maybe_scan_batches()
                await self._maybe_run_retention()
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

    # ------------------------------------------------------------ 攒批记忆
    async def _maybe_scan_batches(self) -> None:
        """到点才发起攒批扫描(默认 60 秒一次)。

        用单调时钟(asyncio 的 loop.time)而不是墙上时钟计时:
        系统时间被改(校时/NTP)不该让扫描停摆或突然连发。
        """
        now = asyncio.get_running_loop().time()
        if now - self._last_batch_scan < BATCH_SCAN_INTERVAL_SECONDS:
            return
        self._last_batch_scan = now
        # 上一轮还没跑完就跳过这一轮: 总结要调 LLM(可能数十秒),
        # 若允许重叠,同一会话会被并发总结 —— 既浪费 token,
        # 也可能让两轮各自基于旧水位线写出重复记忆。
        if self._batch_task is not None and not self._batch_task.done():
            return
        self._batch_task = asyncio.create_task(self._scan_batches())

    # ------------------------------------------------------------ 存储清理
    async def _maybe_run_retention(self) -> None:
        """到点才跑一次存储清理(默认 24 小时一次)。

        为什么低频: 这是维护动作,不产生用户可见的效果 ——
        频繁扫库只会白占 IO,而且删数据间隔太短也看不出"删了哪些"。
        用单调时钟计时,与攒批扫描同理(系统时间被改不影响节奏)。
        """
        interval = max(1, int(get_settings().retention_interval_hours)) * 3600
        now = asyncio.get_running_loop().time()
        if self._last_retention_run and now - self._last_retention_run < interval:
            return
        self._last_retention_run = now
        if self._retention_task is not None and not self._retention_task.done():
            return
        self._retention_task = asyncio.create_task(self._run_retention())

    async def _run_retention(self) -> None:
        """跑一轮存储清理(后台任务)。

        需要 checkpoint 精简时得拿到图实例 —— 它由 main.py 组装后登记在
        agent.runtime 的全局访问点里(这里延迟导入,避免模块级循环依赖)。

        异常就地吞掉: 清理是维护动作,失败只是"这次没清",
        绝不能让调度循环停摆。
        """
        from . import retention
        from .agent.runtime import get_graph

        try:
            result = await retention.apply(graph=get_graph())
        except Exception as exc:  # noqa: BLE001 - 清理失败不能影响调度循环
            print(f"[scheduler] 存储清理失败: {type(exc).__name__}: {exc}")
            return
        if result.get("total_deleted") or result.get("checkpoints_pruned"):
            print(f"[scheduler] 存储清理: 共删除 {result.get('total_deleted', 0)} 行")

    async def _scan_batches(self) -> None:
        """跑一轮攒批扫描(后台任务)。

        不 await 进轮询循环的理由与 _publish_timer 一致: 一次总结可能要跑
        LLM 数十秒,阻塞轮询会让到期的定时唤醒集体迟到。
        异常在这里就地吞掉 —— 攒批失败只是"这批记忆晚点再写",
        绝不能影响调度循环(与 _poll_loop 的 try/except 同一原则)。
        """
        try:
            summary = await batches.run_due_batches()
        except Exception as exc:  # noqa: BLE001 - 攒批失败不能影响调度循环
            print(f"[scheduler] 攒批记忆扫描失败: {type(exc).__name__}: {exc}")
            return

        failed = [run for run in summary.get("runs", []) if run.get("status") == "error"]
        if summary.get("runs"):
            print(
                f"[scheduler] 攒批记忆: 处理 {len(summary['runs'])} 个会话,"
                f"写入 {summary.get('written', 0)} 条记忆"
            )
        for run in failed:
            print(f"[scheduler] 会话 {run.get('conversation_id')} 攒批失败: {run.get('error')}")

        # 刚写过记忆 → 顺手跑一轮"过期/冲突"整理(确定性,零模型成本)。
        # 放在攒批之后: 整理要看的正是刚写进去的这批说法。
        await self._consolidate_memories()

    async def _consolidate_memories(self) -> None:
        """跑一轮记忆整理: 合并重复、应用明确订正、标记并上报冲突。

        异常就地吞掉 —— 整理失败只是"过期事实晚点再清",
        绝不能影响调度循环(与 _scan_batches 同一原则)。
        """
        try:
            summary = await consolidation.run_due()
        except Exception as exc:  # noqa: BLE001 - 整理失败不能影响调度循环
            print(f"[scheduler] 记忆整理失败: {type(exc).__name__}: {exc}")
            return
        if summary.get("consolidated") or summary.get("conflicts"):
            print(
                f"[scheduler] 记忆整理: 处理 {summary['consolidated']} 个会话,"
                f"发现 {summary['conflicts']} 处冲突"
            )

    async def _publish_timer(self, record: dict[str, Any]) -> None:
        """把一条到期记录转成 timer 事件发布(后台任务)。

        timer 事件的关键点: should_respond=True —— 唤醒的目的就是让 agent
        继续干活(否则 agent 只会记一条审计就结束,等待就白设了)。
        但 kind=timer 会让 ingest 不把它写进对话历史(它不是"人说的话")。
        """
        try:
            await get_bus().publish(
                Event.from_text(
                    record["note"] or "定时唤醒",
                    source=EventSource.SCHEDULER,
                    kind=EventKind.TIMER,
                    platform="desktop",
                    chat_type="thread",
                    external_id=record["conversation_id"],
                    conversation_id=record["conversation_id"],
                    should_respond=True,
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