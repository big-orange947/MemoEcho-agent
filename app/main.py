# =============================================================================
# main.py - 应用入口(组装一切)
# -----------------------------------------------------------------------------
# 启动方式:  uv run python -m app.main serve
#
# 组装顺序(依赖方向,从外到内):
#   1. 建表(init_db)
#   2. 创建渠道桥(NapcatBridge)
#   3. 创建 LLM 工厂(LangChain ChatOpenAI,绑定配置)
#   4. 创建工具集(tools/messaging + contacts + memory)
#   5. 创建 AgentGraph(注入 llm 工厂 + 工具 + 发送器)
#   6. 注册事件处理器(EventBus → AgentGraph)
#   7. 挂载 API 路由 + QQ webhook 端点
# =============================================================================

from __future__ import annotations

import asyncio
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from langchain_openai import ChatOpenAI

from .api import routes as api_routes
from .api import sse as sse_api
from .agent.graph import AgentGraph
from .bridge.napcat import NapcatBridge
from .config import get_settings
from .db import close_connections, init_db
from .events import Event, get_bus
from .services import conversations as conversations_service
from .services import goals as goals_service
from .tools import contacts as contacts_tools
from .tools import memory as memory_tools
from .tools import messaging as messaging_tools
from .tools import wait as wait_tools
from .scheduler import get_scheduler


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
            # QQ 会话: 发私聊(群聊支持留作后续扩展)
            await napcat.send_private_message(conv["external_id"], text)
        else:
            # 桌面端: 走 SSE 推给前端
            await sse_api.push("reply", {"conversation_id": conversation_id, "text": text})

    # 6. AgentGraph(注入依赖)
    graph = AgentGraph(llm_factory=llm_factory, tools=tools, sender=sender)

    # 7. 事件处理器: EventBus → AgentGraph
    async def handle_event(event: Event) -> None:
        # 桌面端命令 → 先建目标,再走 agent
        if event.event_type == "command" and event.command:
            conv_id = event.conversation_id or conversations_service.ensure_conversation(
                event.platform, event.chat_type, event.external_id
            )
            goals_service.create_goal(conv_id, event.command)
            event.conversation_id = conv_id
        # 跑图(内部会持久化消息/更新目标/调用发送器)
        reply = await graph.run_event(
            {
                "conversation_id": event.conversation_id,
                "event_type": event.event_type,   # 必须是 message/timer,ingest 靠它分流
                "platform": event.platform,
                "chat_type": event.chat_type,
                "external_id": event.external_id,
                "event_id": event.event_id,
                "text": event.text,
                "sender_id": event.sender_id,
                "is_self": event.is_self,
                "raw": event.raw,
            }
        )
        # 桌面端进度卡: 推送目标状态变化(如有)
        if event.conversation_id:
            goal = goals_service.get_active_goal(event.conversation_id)
            if goal:
                await sse_api.push(
                    "progress",
                    {"conversation_id": event.conversation_id, "goal": goal},
                )

    get_bus().register(handle_event)

    # ------------------------------------------------------------------ FastAPI
    app = FastAPI(title="Memo Echo v2")
    app.include_router(api_routes.router)
    app.include_router(sse_api.router)

    # 定时唤醒调度器: 启动后台任务(每秒轮询 scheduled_events,到点发 timer 事件)
    scheduler = get_scheduler()

    @app.on_event("startup")
    async def _startup() -> None:
        scheduler.start()

    # QQ webhook 端点: NapCat 把事件 POST 到这里(OneBot HTTP 上报)
    @app.post("/qq/webhook")
    async def qq_webhook(request: Request) -> dict[str, Any]:
        body = await request.json()
        post_type = body.get("post_type", "")
        message_type = body.get("message_type", "")

        # 只处理"私聊消息"且非机器人自己发出的(自己发的回显忽略,避免死循环)
        if post_type == "message" and message_type == "private" and not body.get("self_id") == body.get("user_id"):
            event = Event(
                event_type="message",
                platform="qq",
                chat_type="private",
                external_id=str(body.get("user_id", "")),
                text=str((body.get("message") or "")),
                sender_id=str(body.get("user_id", "")),
                is_self=False,
                raw=body,
            )
            await get_bus().publish(event)

        # OneBot 要求返回 {"status":"ok"} 表示已接收
        return {"status": "ok"}

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
