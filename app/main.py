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

from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from langchain_openai import ChatOpenAI

from .api import routes as api_routes
from .api import sse as sse_api
from .agent.graph import AgentGraph, ConversationBusyError
from .agent.runtime import set_graph
from .bridge import onebot
from .bridge.napcat import NapcatBridge
from .config import get_settings
from .db import close_connections, init_db
from .events import Event, EventKind, EventSource, get_bus, reset_bus
from .scheduler import get_scheduler
from .services import conversations as conversations_service
from .services import eventlog
from .services import goals as goals_service
from .tools import contacts as contacts_tools
from .tools import memory as memory_tools
from .tools import messaging as messaging_tools
from .tools import wait as wait_tools


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
    tools = [
        *messaging_tools.create_message_tools(),
        *contacts_tools.create_contact_tools(),
        *memory_tools.create_memory_tools(),
        *wait_tools.create_wait_tools(),   # 等待/定时唤醒: "等10分钟再催"这类需求
    ]

    # 5. 发送器: 把 agent 的回复路由到正确渠道
    async def sender(conversation_id: str, text: str, source: str) -> None:
        conv = conversations_service.get_conversation(conversation_id)
        if not conv:
            return
        if conv["platform"] == "qq":
            # QQ 会话: 私聊/群聊分别发送
            if conv["chat_type"] == "group":
                await napcat.send_group_message(conv["external_id"], text)
            else:
                await napcat.send_private_message(conv["external_id"], text)
        else:
            # 桌面端: 走 SSE 推给前端
            await sse_api.push("reply", {"conversation_id": conversation_id, "text": text})

    # 6. AgentGraph(注入依赖)
    graph = AgentGraph(llm_factory=llm_factory, tools=tools, sender=sender)
    # 登记到全局访问点: API 路由等模块通过 get_graph() 取用(如忙碌预检)
    set_graph(graph)

    # ------------------------------------------------------------------ 事件处理器
    # ① 审计处理器: 所有事件都记一笔(含不需要回应的通知/请求/自发回显)。
    #    注册在最前面 —— 保证"先留痕,再处理",即使后续处理器失败也有记录。
    async def audit_handler(event: Event) -> None:
        eventlog.log_event(event)

    # ② agent 处理器: 只处理需要生成输出的事件。
    async def agent_handler(event: Event) -> None:
        # 不需要回应的事件(通知/请求/自发回显)到此为止,只留了审计记录
        if not event.should_respond:
            return

        # 命令/指令类事件: 先把指令变成会话上的目标(goal),再跑图推进
        if event.kind in (EventKind.COMMAND, EventKind.INSTRUCTION) and event.command:
            conv_id = event.conversation_id or conversations_service.ensure_conversation(
                event.platform, event.chat_type, event.external_id
            )
            goals_service.create_goal(conv_id, event.command)
            event.conversation_id = conv_id

        # 跑图(内部会持久化消息/更新目标/调用发送器)。
        # 事件转成可序列化字典 —— 它会进 LangGraph State 并被 checkpoint 序列化。
        try:
            await graph.run_event(event.to_payload())
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

    # 定时唤醒调度器: 启动后台任务(每秒轮询 scheduled_events,到点发 timer 事件)
    scheduler = get_scheduler()

    @app.on_event("startup")
    async def _startup() -> None:
        scheduler.start()

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
            import asyncio

            asyncio.create_task(get_bus().publish(event))
        else:
            await get_bus().publish(event)

        return {"status": "ok", "retcode": 0}

    # 关闭时释放资源
    @app.on_event("shutdown")
    async def _shutdown() -> None:
        await scheduler.stop()
        await napcat.close()
        await graph.close()
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
