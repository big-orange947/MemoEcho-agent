import asyncio
from contextlib import asynccontextmanager, suppress
import logging

from fastapi import BackgroundTasks, FastAPI, HTTPException

from app.orchestrator.service import OrchestratorService
from app.schemas.conversation_progress import ConversationProgressRequest, ConversationProgressResponse
from app.schemas.conversation_cognition import ConversationCognitionRequest, ConversationCognitionResponse
from app.schemas.events import UnifiedEvent
from app.schemas.delegated_tasks import DelegatedTaskCompileRequest, DelegatedTaskCompileResponse
from app.schemas.delegated_workflows import (
    DelegatedWorkflowStepExecutionRequest,
    DelegatedWorkflowStepExecutionResponse,
)
from app.schemas.results import OrchestratorResult
from app.schemas.group_operations import (
    GroupOperationApprovalResponse,
    GroupOperationEventApprovalRequest,
    PendingGroupOperationResponse,
)
from app.tools.qq_group_operations_tool import ManageQqGroupTool
from app.services.conversation_progress_service import ConversationProgressService
from app.services.conversation_cognition_service import ConversationCognitionService
from app.services.event_execution_registry import EventExecutionRegistry


# 使用 Uvicorn 的错误日志器，确保首次自动下载和预热模型时用户能在启动控制台看到进度结果。
logger = logging.getLogger("uvicorn.error")
orchestrator = OrchestratorService.build_default()
conversation_progress_service = ConversationProgressService(
    orchestrator.event_center_client,
    orchestrator.llm_client,
)
conversation_cognition_service = ConversationCognitionService(
    orchestrator.event_center_client,
    orchestrator.llm_client,
)
event_execution_registry = EventExecutionRegistry()


async def _warm_up_builtin_embedding() -> None:
    """在后台预热内置向量模型；失败时保留原有关键词路由，不阻止运行时启动。"""
    classifier = orchestrator.schedule_intent_classifier
    if classifier is None:
        return
    try:
        logger.info("正在预热日程语义门控。backend=%s", classifier.client.backend_name())
        await classifier.warm_up()
        logger.info("日程语义门控预热完成。backend=%s", classifier.client.backend_name())
    except Exception as exception:
        logger.warning("日程语义门控预热失败，将暂时使用原有路由。error=%s", exception)


@asynccontextmanager
async def lifespan(_: FastAPI):
    """管理运行时后台资源，在服务启动后自动加载无需配置的本地向量模型。"""
    warm_up_task = asyncio.create_task(_warm_up_builtin_embedding())
    yield
    if not warm_up_task.done():
        warm_up_task.cancel()
        with suppress(asyncio.CancelledError):
            await warm_up_task


app = FastAPI(title="Memo Echo Agent Runtime", version="0.1.0", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/events/handle", response_model=OrchestratorResult)
async def handle_event(event: UnifiedEvent, background_tasks: BackgroundTasks) -> OrchestratorResult:
    """处理实时事件；代理私聊中的附件解析在响应后后台执行，不能阻塞自动回复或人工审批。"""
    try:
        result, reused = await event_execution_registry.execute(
            event.event_id,
            lambda: orchestrator.handle_event(event),
        )
    except Exception:
        # 保留原始异常响应给调用方，同时记录事件定位信息，避免客户端只看到笼统的 500。
        logger.exception(
            "事件处理失败：eventId=%s, platform=%s, eventType=%s, chatId=%s",
            event.event_id,
            event.platform,
            event.event_type,
            event.chat_id,
        )
        raise
    # 私聊社交回复已在主链路同步完成附件理解，避免响应后重复调用视觉模型。
    # 重复事件已经复用第一次执行结果，因此也不能重复创建附件后台任务。
    if not reused and event.attachments and result.route not in {"file_analysis", "social_reply"}:
        background_tasks.add_task(orchestrator.analyze_attachments_in_background, event)
    return result


@app.post("/v1/delegated-tasks/compile", response_model=DelegatedTaskCompileResponse)
async def compile_delegated_task(
    request: DelegatedTaskCompileRequest,
) -> DelegatedTaskCompileResponse:
    """把工作台自然语言命令编译为受控委托；这里只理解任务，不执行外部动作。"""
    model_profile = None
    if orchestrator.event_center_client is not None:
        try:
            resolved = await orchestrator.event_center_client.resolve_user_model_profile(
                route="task_plan",
                user_id=request.user_id,
            )
            if resolved.matched:
                model_profile = resolved.profile
        except Exception as exception:
            # Event Center 或模型配置暂时不可用时继续走图内保守规则，不阻断用户创建任务。
            logger.warning("委托任务模型解析失败，改用本地规则。error=%s", type(exception).__name__)
    return await orchestrator.delegated_task_workflow.compile_task(request, model_profile)


@app.post(
    "/v1/delegated-workflows/steps/execute",
    response_model=DelegatedWorkflowStepExecutionResponse,
)
async def execute_delegated_workflow_step(
    request: DelegatedWorkflowStepExecutionRequest,
) -> DelegatedWorkflowStepExecutionResponse:
    """消费 Java outbox 的显式步骤触发，过期激活版本只确认消费而不产生副作用。"""
    return await orchestrator.execute_delegated_workflow_step(request)


@app.post("/v1/conversations/progress", response_model=ConversationProgressResponse)
async def summarize_conversation_progress(
    request: ConversationProgressRequest,
) -> ConversationProgressResponse:
    """仅在桌面端打开上下文时分析一次当前进度，不创建事件，也不会回写聊天平台。"""
    return await conversation_progress_service.summarize(request)


@app.post("/v1/conversations/cognition", response_model=ConversationCognitionResponse)
async def analyze_conversation_cognition(
    request: ConversationCognitionRequest,
) -> ConversationCognitionResponse:
    """仅在用户主动刷新认知卡时分析一次会话，不创建事件，也不向聊天平台发送消息。"""
    return await conversation_cognition_service.analyze(request)


@app.get("/v1/group-operations/pending/{event_id}", response_model=PendingGroupOperationResponse)
async def get_pending_group_operation(event_id: str) -> PendingGroupOperationResponse:
    """按事件返回不含令牌的审批摘要；该接口只应由本机 Event Center 调用。"""
    tool = orchestrator.tools.get_internal_service("manage_qq_group")
    if not isinstance(tool, ManageQqGroupTool):
        raise HTTPException(status_code=503, detail="群管理工具未加载")
    proposal = tool.pending_for_event(event_id)
    if proposal is None:
        raise HTTPException(status_code=404, detail="待审批群操作不存在或已过期")
    return PendingGroupOperationResponse.model_validate(proposal)


@app.post(
    "/v1/group-operations/approve-event/{event_id}",
    response_model=GroupOperationApprovalResponse,
)
async def approve_group_operation_by_event(
    event_id: str,
    request: GroupOperationEventApprovalRequest,
) -> GroupOperationApprovalResponse:
    """按事件消费 Runtime 内部令牌，桌面客户端无需接触高权限凭据。"""
    tool = orchestrator.tools.get_internal_service("manage_qq_group")
    if not isinstance(tool, ManageQqGroupTool):
        raise HTTPException(status_code=503, detail="群管理工具未加载")
    try:
        result = await tool.approve_event(event_id, request.confirmation_text)
    except ValueError as exception:
        raise HTTPException(status_code=409, detail=str(exception)) from exception
    return GroupOperationApprovalResponse.model_validate(result)
