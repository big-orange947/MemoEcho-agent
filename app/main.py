# =============================================================================
# main.py - 应用入口(组装一切)
# -----------------------------------------------------------------------------
# 启动方式:  uv run python -m app.main serve
#
# 组装顺序(依赖方向,从外到内):
#   1. 建表(init_db)
#   2. 创建渠道桥(NapcatBridge)
#   3. 创建 LLM 工厂(LangChain ChatOpenAI,绑定配置)
#   4. 创建工具集(messaging + contacts + memory + wait)
#   5. 创建 AgentGraph(注入 llm 工厂 + 工具 + 发送器)
#   6. 注册事件处理器(审计日志 + AgentGraph)
#   7. 挂载 API 路由 + QQ webhook 端点
#
# 事件处理的两级结构(重要):
#   EventBus 上注册了两个处理器,按顺序执行:
#     ① audit_handler  —— 所有事件都记一笔审计(含"不需要回应"的)
#     ② agent_handler  —— 只有 should_respond=True 的事件才跑图
#   这样"收到撤回通知"与"收到聊天消息"走同一条入站通道,
#   但只有后者会触发回复,前者只留痕。
# =============================================================================

from __future__ import annotations

import asyncio
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from langchain_openai import ChatOpenAI

from . import memory as memory_layer
from . import recorder
from . import reports as reports_service
from . import sinks as sinks_service
from .api import dispatch as dispatch_api
from .api import reports as reports_api
from .api import routes as api_routes
from .api import sse as sse_api
from .agent.graph import AgentGraph, ConversationBusyError
from .agent.runtime import set_graph, set_sender
from .bridge import onebot
from .bridge.napcat import NapcatBridge
from .config import get_settings
from .db import close_connections, init_db
from .events import Event, EventKind, EventSource, get_bus, reset_bus
from . import outbox
from .scheduler import get_scheduler
from .services import conversations as conversations_service
from .services import dispatches as dispatch_service
from .services import eventlog
from .services import goals as goals_service
from .services import policy as policy_service
from .tools import contacts as contacts_tools
from .tools import escalate as escalate_tools
from .tools import messaging as messaging_tools
from .tools import wait as wait_tools

# 上报队列后台 worker 的轮询间隔(秒)。
# 复核要"攒批"(省模型调用),所以这里不需要高频;25 秒足以让候选及时上报,
# 又远低于人类感知阈值。
REPORTS_WORKER_INTERVAL = 25.0


# ---------------------------------------------------------------------------
# 组装器: 创建整个应用(供 FastAPI 与测试复用)
# ---------------------------------------------------------------------------
def create_app() -> FastAPI:
    """构建 FastAPI 应用并完成全部依赖注入。"""
    settings = get_settings()
    init_db()  # 1. 确保表存在

    # 2. 渠道桥(QQ)
    napcat = NapcatBridge()

    # 3. LLM 工厂: fast=True 用轻量模型(判断/评估),False 用主模型(对话)
    def llm_factory(fast: bool = False) -> ChatOpenAI:
        return ChatOpenAI(
            model=settings.fast_model_name if fast else settings.model_name,
            temperature=0.2 if fast else 0.7,
            timeout=settings.llm_timeout_seconds,
            # 凭据与接口地址从 config.py 读取(.env 的 OPENAI_API_KEY/OPENAI_BASE_URL),
            # 显式传入,避免依赖"系统环境变量恰好存在"的隐式约定
            api_key=settings.api_key or None,
            base_url=settings.base_url or None,
        )

    # 4. 工具集(全部 LangChain @tool)
    #    注意: 这里注册的是**全集**;每个会话实际能用哪些由策略决定
    #    (见 services/policy.resolve_allowed_tools 与 graph 的按事件解析)。
    tools = [
        *messaging_tools.create_message_tools(),
        *contacts_tools.create_contact_tools(),
        *escalate_tools.create_escalate_tools(),  # 请示号主: 拿不准的事不擅自拍板
        *wait_tools.create_wait_tools(),   # 等待/定时唤醒: "等10分钟再催"这类需求
    ]

    # 4b. 重要消息复核器(上报流水线的第二级)
    #     规则初筛命中后,用**快模型**把一批候选合并成一次调用,判断值不值得惊动人。
    #     为什么要复核: 规则会误报("@我"也可能只是打个招呼),而误报会消耗号主的注意力。
    #     为什么批量: 每条候选单独调一次模型成本不可接受;合并成一次调用是成本关键。
    #     失败怎么办: 抛异常会被 reports.flush_candidates 捕获并**退化为纯规则** ——
    #                 宁可多报一条,也不能因为模型抖动把急事漏掉。
    #     实现见 app/reports.py(提示词与解析都在那里,便于单测与冒烟验证)。
    reports_reviewer = reports_service.build_default_reviewer(llm_factory)

    # 4c. 上报队列后台 worker
    #     每隔一段时间做四件事(前三件在 app/reports.py、第四件在 app/sinks.py,
    #     这里只负责定时触发):
    #       ① 复核候选并把它们提升为可消费状态(攒够批或超时);
    #       ② 回收过期租约(上游认领后崩了 → 消息回队列,不丢);
    #       ③ 清理已处理的历史记录(队列不无限增长);
    #       ④ 把待投递的记录送到配置的出口(qq 转发;未配置时什么也不做)。
    async def reports_worker() -> None:
        while True:
            try:
                await asyncio.sleep(REPORTS_WORKER_INTERVAL)
                await reports_service.flush_candidates(reviewer=reports_reviewer)
                reports_service.reap_expired()
                await sinks_service.deliver_pending(send=alert_forward_sender)
                reports_service.purge_resolved()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 后台任务不能因单次异常退出
                print(f"[reports] worker 异常: {type(exc).__name__}: {exc}")

    # 4d. 上报出口的投递函数(把通知发到指定的 QQ 会话)
    #     走 outbox 统一投递: 先落库(这条通知也进目标会话的历史),再发送 ——
    #     与"agent 回复"走同一条路径,不另开一套发送逻辑。
    async def alert_forward_sender(conversation_id: str, text: str) -> bool:
        result = await outbox.deliver(conversation_id, text, source="alert")
        return bool(result.get("ok"))

    # 5. 发送器: 把 agent 的回复路由到正确渠道
    async def sender(conversation_id: str, text: str, source: str) -> dict[str, Any] | None:
        """把回复发到会话所属渠道。

        返回值: 平台发送结果(含 platform_message_id),桌面端返回 None。
        为什么要把结果传出来: 调用方(outbox / finalize)据此回填平台消息 ID,
        这样平台把这条消息回显回来时能被识别成"自己发的",不重复入库。
        """
        conv = conversations_service.get_conversation(conversation_id)
        if not conv:
            return None
        if conv["platform"] == "qq":
            # QQ 会话: 私聊/群聊分别发送
            if conv["chat_type"] == "group":
                return await napcat.send_group_message(conv["external_id"], text)
            return await napcat.send_private_message(conv["external_id"], text)
        # 桌面端: 走 SSE 推给前端
        await sse_api.push("reply", {"conversation_id": conversation_id, "text": text})
        return None

    # 5b. 联系人发送器: 供"给其他人发消息"的工具使用(messaging 工具)
    #     与 sender 的区别:
    #       sender            —— 发给**当前会话**(finalize 用)
    #       contact_sender    —— 发给**指定的其他人**(LLM 主动传话用)
    #
    #     关键设计: 发给别人时,也在**那个人的会话**里落一条出站记录。
    #     这样对方回复时,上下文才对得上 ——
    #     否则"问小号"发出去后,小号的回复会进入一个没有任何上下文的空会话,
    #     agent 根本不知道这是在回答我们问的问题。
    async def contact_sender(
        platform: str,
        chat_type: str,
        external_id: str,
        text: str,
        origin_conversation_id: str = "",
    ) -> bool:
        try:
            # 定位(或创建)目标联系人/群的会话
            conversation_id = conversations_service.ensure_conversation(
                platform, chat_type, external_id
            )
            # 任务延伸: 若这次外联是某个目标驱动的,把被联系的会话也登记到该目标上。
            # 为什么必须做: 对方的回复落在**这个**会话里,若不登记,
            # 那条回复既没有目标撑腰、会话也没开自动回复,会被直接丢掉 ——
            # "帮我问 km 然后转告小号"这类任务就永远等不到回音。
            if origin_conversation_id:
                goal = goals_service.get_active_goal_involving(origin_conversation_id)
                if goal:
                    goals_service.link_conversation(str(goal.get("id") or ""), conversation_id)
            # 走 outbox 统一投递: 先落库(记进该会话历史),再发送
            result = await outbox.deliver(conversation_id, text, source="outbound")
            return bool(result.get("ok"))
        except Exception as exc:  # noqa: BLE001 - 工具层拿到的应是 False,不是异常
            print(f"[sender] 给 {platform}/{chat_type}/{external_id} 发送失败: {type(exc).__name__}: {exc}")
            return False

    messaging_tools.init_sender(contact_sender)

    # 6. AgentGraph(注入依赖)
    graph = AgentGraph(llm_factory=llm_factory, tools=tools, sender=sender)
    # 登记到全局访问点: API 路由/outbox 等模块通过 get_graph()/get_sender() 取用
    set_graph(graph)
    set_sender(sender)

    # ------------------------------------------------------------------ 事件处理器
    # ① 审计处理器: 所有事件都记一笔(含不需要回应的通知/请求/自发回显)。
    #    注册在最前面 —— 保证"先留痕,再处理",即使后续处理器失败也有记录。
    async def audit_handler(event: Event) -> None:
        # 尽量把会话填进审计行: 排障时"这条消息属于哪个会话"是最常用的入口。
        # 用 find_conversation(只查不建)—— 审计层不该产生副作用;
        # 首次出现的会话此时还没落库,下一次事件就能关联上了。
        conversation_id = event.conversation_id
        if not conversation_id and event.external_id:
            found = conversations_service.find_conversation(
                event.platform, event.chat_type, event.external_id
            )
            conversation_id = found["id"] if found else ""
        eventlog.log_event(event, conversation_id=conversation_id)

    # ② agent 处理器: 按会话策略分流 —— 跑图 / 只记录 / 忽略。
    #
    #    分流依据是**会话策略**(services/policy.py)而不是平台规则本身:
    #      · 显式指令(主 agent 派发、桌面端命令)优先级最高,不受开关约束;
    #      · 任务授权态(该会话有进行中的目标)期间可自由交流;
    #      · 其余平台消息: reply_mode=auto 才回复;仅监视则只落库(零模型成本);
    #      · 全关 → 只留审计(由 audit_handler 记录)。
    async def _evaluate_alert(conversation: dict[str, Any], conversation_id: str, event: Event) -> None:
        """对**对方发来的**消息做上报评估(规则初筛,零模型成本)。

        只评估"别人说的话": 号主自己发的消息(message_sent)、
        平台通知、定时唤醒都不该触发上报 —— 那不是"新发生的事"。
        """
        if event.kind not in (EventKind.MESSAGE, EventKind.INSTRUCTION):
            return
        if event.is_self:
            return
        if not policy_service.normalize_policy(conversation).get("alert_enabled"):
            return

        outcome = reports_service.observe(
            conversation,
            conversation_id=conversation_id,
            message_id=event.event_id,   # 与 ingest 落库用的 ID 一致 → 幂等
            text=event.text,
            sender_id=event.sender_id,
            sender_name=event.sender_name,
        )
        if outcome.get("matched"):
            # 桌面端实时可见(只推，不做 UI)：前端据此提示"聊到值得注意的事了"
            await sse_api.push(
                "report",
                {
                    "conversation_id": conversation_id,
                    "reasons": outcome.get("reasons") or [],
                    "text": event.text[:200],
                },
            )

    async def agent_handler(event: Event) -> None:
        # ---- 0. 定位会话(策略是按会话存的,先拿到会话行才能判定) ----
        conversation_id = event.conversation_id or ""
        if conversation_id:
            # 保底建号: dispatch 可以直接给一个尚未落库的会话 ID
            conversations_service.ensure_conversation_by_id(conversation_id)
        elif event.external_id:
            conversation_id = conversations_service.ensure_conversation(
                event.platform, event.chat_type, event.external_id
            )
        conversation = (
            conversations_service.get_conversation(conversation_id) or {} if conversation_id else {}
        )
        # 用 involving 版本: 除了"目标挂在本会话",还包括"agent 为了完成某个任务
        # 主动联系过本会话"—— 对方的回复因此能继续推进任务(见 services/goals)。
        active_goal = goals_service.get_active_goal_involving(conversation_id) if conversation_id else None
        has_active_goal = bool(active_goal)

        decision, reason = policy_service.decide(event, conversation, has_active_goal=has_active_goal)
        if decision == policy_service.DECISION_IGNORE:
            return

        event.conversation_id = conversation_id

        # ---- 只记录: 写进对话历史供攒批总结/上报,不跑图、不调模型 ----
        if decision == policy_service.DECISION_RECORD:
            result = await recorder.record(conversation_id, event.to_payload())
            if result.get("recorded"):
                eventlog.log_event(
                    Event(
                        event_id=f"record-{event.event_id}",
                        source=EventSource.SYSTEM,
                        kind=EventKind.SYSTEM,
                        should_respond=False,
                        conversation_id=conversation_id,
                        text=f"仅记录({reason}): {event.text[:60]}",
                    ),
                    conversation_id=conversation_id,
                )
                # 重要消息上报: 规则初筛(零成本)命中的先入队为候选,
                # 由后台 worker 用快模型批量复核后决定最终去向。
                await _evaluate_alert(conversation, conversation_id, event)
            return

        # ---- 以下为跑图路径(记录由图内 ingest 负责,不能在这里预记录) ----
        # 命令/指令类事件: 先把指令变成会话上的目标(goal),再跑图推进
        if event.kind in (EventKind.COMMAND, EventKind.INSTRUCTION) and event.command:
            conv_id = event.conversation_id or conversations_service.ensure_conversation(
                event.platform, event.chat_type, event.external_id
            )
            goals_service.create_goal(conv_id, event.command)
            event.conversation_id = conv_id

        # 自动回复的会话同样要做上报评估: 开着上报开关时,
        # "agent 已经回了"不代表"号主不需要知道" —— 两件事互不替代。
        # 放在跑图之前: 规则判断是纯字符串操作,不拖慢回复;而且即便回复失败,
        # 这条重要消息也不会因为异常而漏报。
        await _evaluate_alert(conversation, conversation_id, event)

        # 跑图(内部会持久化消息/更新目标/调用发送器)。
        # 事件转成可序列化字典 —— 它会进 LangGraph State 并被 checkpoint 序列化。
        # task_id(调度任务)在事件 context 里: 执行完要回写任务状态与产物。
        task_id = str((event.context or {}).get("task_id") or "")
        try:
            if task_id:
                dispatch_service.mark_running(task_id)
            reply = await graph.run_event(event.to_payload())
            if task_id:
                dispatch_service.mark_done(
                    task_id,
                    {"reply": reply or "", "conversation_id": event.conversation_id},
                )
        except ConversationBusyError as exc:
            # 会话拥堵: 丢弃本次事件并留痕(不重试 —— 重试只会让队列更长)。
            # 用户端表现为"这条消息没回",审计表里能查到原因。
            print(f"[agent] 会话繁忙,丢弃事件 {event.event_id}: {exc}")
            eventlog.log_event(
                Event(
                    event_id=f"busy-{event.event_id}",
                    source=EventSource.SYSTEM,
                    kind=EventKind.SYSTEM,
                    should_respond=False,
                    conversation_id=exc.conversation_id,
                    text=f"会话繁忙丢弃: {event.text[:60]}",
                ),
                conversation_id=exc.conversation_id,
            )
            if task_id:
                dispatch_service.mark_busy(task_id)
        except Exception as exc:  # noqa: BLE001 - 单次执行失败不该让事件总线崩掉
            # 非拥堵类失败(如模型超时、工具异常): 记录日志 + 回写任务状态。
            # 事件本身不回滚 —— ingest 可能已经落库了入站消息,保留现场更利于排查。
            print(f"[agent] 事件处理失败 {event.event_id}: {type(exc).__name__}: {exc}")
            if task_id:
                dispatch_service.mark_failed(task_id, f"{type(exc).__name__}: {exc}")

        # 桌面端进度卡: 推送目标状态变化(如有)
        if event.conversation_id:
            goal = goals_service.get_active_goal(event.conversation_id)
            if goal:
                await sse_api.push(
                    "progress",
                    {"conversation_id": event.conversation_id, "goal": goal},
                )

    # 注册顺序即执行顺序: 先审计,后处理。
    # 注意: 必须先 reset_bus() —— 处理器注册在全局单例上,create_app 可能被
    # 调用多次(模块级组装 + 测试/重载),不重置会导致事件被重复处理。
    bus = reset_bus()
    bus.register(audit_handler)
    bus.register(agent_handler)

    # ------------------------------------------------------------------ FastAPI
    app = FastAPI(title="Memo Echo v2")
    app.include_router(api_routes.router)
    app.include_router(sse_api.router)
    app.include_router(dispatch_api.router)   # 外部调度入口(主 agent 派活)
    app.include_router(reports_api.router)    # 上报队列出口(上游消费)

    # 定时唤醒调度器: 启动后台任务(每秒轮询 scheduled_events,到点发 timer 事件)
    scheduler = get_scheduler()

    @app.on_event("startup")
    async def _startup() -> None:
        scheduler.start()
        # 上报队列 worker: 复核候选 / 回收过期租约 / 清理历史
        app.state.reports_task = asyncio.create_task(reports_worker())

    # ------------------------------------------------------------------ QQ webhook
    # NapCat 把事件 POST 到这里(OneBot HTTP 上报)。
    # 处理原则:
    #   1. **快速返回** —— OneBot 等待响应有超时,慢会被判失败重推;
    #      因此这里只做"解析 + 入总线",真正跑图在后台事件循环里进行。
    #   2. **全类型接收** —— message/message_sent/notice/request 都收,
    #      由 onebot 解析器决定分流(should_respond)。
    #   3. **响应格式** —— 返回 OneBot 约定的 {"status":"ok","retcode":0}。
    @app.post("/qq/webhook")
    async def qq_webhook(request: Request) -> dict[str, Any]:
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001 - 载荷非法时也要给 OneBot 一个明确响应
            return {"status": "failed", "retcode": 1400, "message": "invalid json"}

        # 解析成统一事件(元事件/心跳返回 None,不进入业务链路)
        event = onebot.build_event(
            body,
            bot_qq=settings.bot_qq,
            group_require_at=settings.qq_group_require_at,
        )
        if event is None:
            return {"status": "ok", "retcode": 0}

        # 入总线: 审计 + (可选)跑图。
        # 注意这里 await 了完整处理流程 —— 若图执行很慢会让 OneBot 等超时,
        # 所以用后台任务发布,立刻返回 OK。
        # 例外: 测试/调试时希望同步看到结果,可用 settings.webhook_async=False。
        if settings.webhook_async:
            asyncio.create_task(get_bus().publish(event))
        else:
            await get_bus().publish(event)

        return {"status": "ok", "retcode": 0}

    # 关闭时释放资源
    @app.on_event("shutdown")
    async def _shutdown() -> None:
        reports_task = getattr(app.state, "reports_task", None)
        if reports_task is not None:
            reports_task.cancel()
            try:
                await reports_task
            except asyncio.CancelledError:
                pass
        await scheduler.stop()
        await napcat.close()
        await graph.close()
        # 关闭记忆客户端(Doppel 的 SQLite 连接)
        await memory_layer.close_client()
        close_connections()

    return app


# ---------------------------------------------------------------------------
# CLI 入口: python -m app.main serve
# ---------------------------------------------------------------------------
def cli() -> None:
    """命令行入口: 目前只有 serve(启动服务)。"""
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "serve":
        settings = get_settings()
        print(f"[memo-echo] API: http://{settings.api_host}:{settings.api_port}")
        print(f"[memo-echo] 数据目录: {settings.data_dir.resolve()}")
        # 直接传 app 对象(而不是 "app.main:app" 字符串):
        # 避免 uvicorn 二次导入模块导致 create_app() 跑两遍
        uvicorn.run(app, host=settings.api_host, port=settings.api_port, reload=False)
    else:
        print("用法: python -m app.main serve")


# FastAPI 入口(app 对象,uvicorn app.main:app 或 create_app() 使用)
app = create_app()


if __name__ == "__main__":
    cli()
