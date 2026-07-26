from __future__ import annotations

"""运行时可执行工具的 LangChain 定义。

这里是 Python Agent 可以调用外部能力的唯一入口。每个能力都使用
``langchain_core.tools.tool`` 声明，因此模型、工作流和审计目录看到的是同一种
BaseTool 对象；具体 HTTP 客户端仅作为闭包依赖注入，不再向 Agent 暴露旧的
``execute`` 协议。
"""

from typing import Any

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field

from app.tools.base import ToolSpec
from app.schemas.events import UnifiedEvent


class _PayloadInput(BaseModel):
    """承载后端 DTO 的通用输入，保留现有服务接口的字段形状。"""

    payload: dict[str, Any] = Field(description="传给后端服务的结构化业务数据")


class _TaskListInput(BaseModel):
    """查询任务列表时可选的筛选条件。"""

    params: dict[str, Any] = Field(default_factory=dict, description="任务筛选条件")


class _RecentMessagesInput(BaseModel):
    """读取单个会话最近消息的受限输入。"""

    chat_id: str = Field(description="会话或群聊 ID")
    platform: str = Field(default="qq", description="消息平台")
    chat_type: str = Field(description="private 或 group")
    limit: int = Field(default=20, ge=1, le=100, description="读取条数")


class _SendQqMessageInput(BaseModel):
    """QQ 文本或 OneBot 消息段发送输入。"""

    chat_type: str = Field(description="private 或 group")
    chat_id: str = Field(description="目标好友或群聊 ID")
    message: str | None = Field(default=None, description="纯文本消息")
    segments: list[dict[str, Any]] | None = Field(default=None, description="OneBot 消息段")
    client_message_id: str | None = Field(default=None, description="发送幂等标识")
    correlation_id: str | None = Field(default=None, description="关联事件标识")


class _SecureAssetInput(BaseModel):
    """发送已被会话配置授权的敏感资产输入。"""

    asset_id: str = Field(description="资产 ID")
    user_id: str = Field(description="资产所属用户 ID")
    chat_type: str = Field(description="private 或 group")
    chat_id: str = Field(description="目标会话 ID")
    allowed_asset_ids: list[str] = Field(description="当前会话允许使用的资产白名单")


class _GroupQueryInput(BaseModel):
    """低风险 QQ 群只读查询输入。"""

    action: str = Field(description="群信息、成员、公告等只读动作")
    group_id: int = Field(gt=0, description="群号")


class _GroupManageInput(BaseModel):
    """高风险群管理动作只创建待审批提案，不直接执行。"""

    action: str = Field(description="群管理动作")
    event_id: str = Field(description="触发该动作的事件 ID")
    requester_id: str = Field(description="提出请求的用户 ID")
    group_id: int = Field(gt=0, description="群号")
    target_user_id: int | None = Field(default=None, description="目标成员 QQ 号")
    duration_seconds: int | None = Field(default=None, description="禁言秒数")
    text: str | None = Field(default=None, description="公告、群名或名片文本")
    enable: bool | None = Field(default=None, description="开关类动作的目标状态")
    message_id: int | None = Field(default=None, description="精华消息 ID")
    reject_add_request: bool | None = Field(default=None, description="踢人时是否拒绝再次加群")


class _DelegatedTaskRuntimeInput(BaseModel):
    """主控台委托任务的状态回写输入，只允许 LangGraph 工作流调用。"""

    event: dict[str, Any] = Field(description="触发状态变更的统一事件")
    task_id: str = Field(description="委托任务 ID")
    progress_summary: str = Field(default="", description="当前任务进度摘要")
    state_json: str = Field(default="{}", description="可恢复的工作流状态 JSON")
    last_event_id: str = Field(default="", description="已处理的最后事件 ID")
    completion_report: str = Field(default="", description="任务完成后的汇报内容")


def build_runtime_tools(
    *,
    event_center_client: Any,
    schedule_service_client: Any,
    task_service_client: Any,
    connector_client: Any,
    file_text_extractor: Any,
    secure_asset_sender: Any,
    group_query_manager: Any,
    group_operation_manager: Any,
) -> list[BaseTool]:
    """构建运行期唯一使用的一组 LangChain ``@tool`` 工具。

    这些闭包只负责输入校验和受控的服务调用。权限、幂等和审计由 ToolRegistry
    统一处理，因此 Agent 不能通过直接访问客户端绕开策略层。
    """

    @tool("extract_file_text")
    async def extract_file_text(
        attachments: list[dict[str, Any]],
        message_text: str = "",
    ) -> dict[str, Any]:
        """提取附件中的文本内容，用于文件理解和后续 Agent 分析。"""
        return await file_text_extractor.extract(attachments=attachments, message_text=message_text)

    @tool("get_recent_messages", args_schema=_RecentMessagesInput)
    async def get_recent_messages(
        chat_id: str,
        chat_type: str,
        platform: str = "qq",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """读取指定会话最近消息，仅用于理解上下文。"""
        return await event_center_client.list_conversation_messages(
            chat_id=str(chat_id),
            platform=platform,
            chat_type=chat_type,
            limit=int(limit),
        )

    @tool("create_schedule", args_schema=_PayloadInput)
    async def create_schedule(payload: dict[str, Any]) -> dict[str, Any]:
        """创建经过 Agent 提取和用户策略允许的日程。"""
        return await schedule_service_client.create_schedule(payload)

    @tool("create_task", args_schema=_PayloadInput)
    async def create_task(payload: dict[str, Any]) -> dict[str, Any]:
        """创建工作任务。"""
        return await task_service_client.create_task(payload)

    @tool("list_tasks", args_schema=_TaskListInput)
    async def list_tasks(params: dict[str, Any]) -> dict[str, Any]:
        """查询已有任务，避免重复创建。"""
        return await task_service_client.list_tasks(params)

    @tool("send_qq_message", args_schema=_SendQqMessageInput)
    async def send_qq_message(
        chat_type: str,
        chat_id: str,
        message: str | None = None,
        segments: list[dict[str, Any]] | None = None,
        client_message_id: str | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """向指定 QQ 私聊或群聊发送文本或 OneBot 消息段。"""
        if chat_type not in {"private", "group"}:
            raise ValueError("chat_type must be private or group")
        if not message and not segments:
            raise ValueError("message or segments is required")
        if chat_type == "group":
            return await connector_client.send_group_message(
                group_id=int(chat_id),
                message=message,
                segments=segments,
                client_message_id=client_message_id,
                correlation_id=correlation_id,
            )
        return await connector_client.send_private_message(
            user_id=int(chat_id),
            message=message,
            segments=segments,
            client_message_id=client_message_id,
            correlation_id=correlation_id,
        )

    @tool("send_secure_asset", args_schema=_SecureAssetInput)
    async def send_secure_asset(
        asset_id: str,
        user_id: str,
        chat_type: str,
        chat_id: str,
        allowed_asset_ids: list[str],
    ) -> dict[str, Any]:
        """在当前会话明确授权后发送收款码、卡密或文件等受保护资产。"""
        return await secure_asset_sender.send(
            asset_id=asset_id,
            user_id=user_id,
            chat_type=chat_type,
            chat_id=chat_id,
            allowed_asset_ids=allowed_asset_ids,
        )

    @tool("query_qq_group", args_schema=_GroupQueryInput)
    async def query_qq_group(action: str, group_id: int) -> dict[str, Any]:
        """查询群信息、成员、公告等低风险只读内容。"""
        return await group_query_manager.query(action=action, group_id=group_id)

    @tool("manage_qq_group", args_schema=_GroupManageInput)
    async def manage_qq_group(
        action: str,
        event_id: str,
        requester_id: str,
        group_id: int,
        target_user_id: int | None = None,
        duration_seconds: int | None = None,
        text: str | None = None,
        enable: bool | None = None,
        message_id: int | None = None,
        reject_add_request: bool | None = None,
    ) -> dict[str, Any]:
        """创建高风险群管理动作的待审批提案，不会直接修改群状态。"""
        return await group_operation_manager.prepare(
            action=action,
            event_id=event_id,
            requester_id=requester_id,
            group_id=group_id,
            target_user_id=target_user_id,
            duration_seconds=duration_seconds,
            text=text,
            enable=enable,
            message_id=message_id,
            reject_add_request=reject_add_request,
        )

    @tool("update_delegated_task", args_schema=_DelegatedTaskRuntimeInput)
    async def update_delegated_task(
        event: dict[str, Any],
        task_id: str,
        progress_summary: str = "",
        state_json: str = "{}",
        last_event_id: str = "",
        completion_report: str = "",
    ) -> dict[str, Any]:
        """回写主控台委托任务的进行中状态，供重启后恢复和前端展示。"""
        unified_event = UnifiedEvent.model_validate(event)
        return await event_center_client.update_delegated_task_runtime(
            unified_event,
            task_id,
            status="ACTIVE",
            progress_summary=progress_summary,
            state_json=state_json,
            last_event_id=last_event_id or unified_event.event_id,
            completion_report=completion_report,
        )

    @tool("complete_delegated_task", args_schema=_DelegatedTaskRuntimeInput)
    async def complete_delegated_task(
        event: dict[str, Any],
        task_id: str,
        progress_summary: str = "",
        state_json: str = "{}",
        last_event_id: str = "",
        completion_report: str = "",
    ) -> dict[str, Any]:
        """结束主控台创建的委托任务，并保存可展示的完成汇报。"""
        unified_event = UnifiedEvent.model_validate(event)
        return await event_center_client.update_delegated_task_runtime(
            unified_event,
            task_id,
            status="COMPLETED",
            progress_summary=progress_summary,
            state_json=state_json,
            last_event_id=last_event_id or unified_event.event_id,
            completion_report=completion_report,
        )

    return [
        extract_file_text,
        get_recent_messages,
        create_schedule,
        create_task,
        list_tasks,
        send_qq_message,
        send_secure_asset,
        query_qq_group,
        manage_qq_group,
        update_delegated_task,
        complete_delegated_task,
    ]


def runtime_tool_specs() -> dict[str, ToolSpec]:
    """声明运行时工具的权限与副作用，供注册表和 Planner 共用。"""
    return {
        "extract_file_text": ToolSpec("extract_file_text", "file.text.extract"),
        "get_recent_messages": ToolSpec("get_recent_messages", "conversation.history.read"),
        "create_schedule": ToolSpec("create_schedule", "schedule.create", side_effect=True),
        "create_task": ToolSpec("create_task", "task.create", side_effect=True),
        "list_tasks": ToolSpec("list_tasks", "task.read"),
        "send_qq_message": ToolSpec("send_qq_message", "chat.message.send", side_effect=True),
        "send_secure_asset": ToolSpec(
            "send_secure_asset",
            "chat.secure_asset.send",
            side_effect=True,
            requires_confirmation=True,
        ),
        "query_qq_group": ToolSpec("query_qq_group", "qq.group.read"),
        "manage_qq_group": ToolSpec(
            "manage_qq_group",
            "qq.group.manage",
            side_effect=True,
            requires_confirmation=True,
        ),
        "update_delegated_task": ToolSpec(
            "update_delegated_task",
            "workspace.delegated_task.update",
            side_effect=True,
        ),
        "complete_delegated_task": ToolSpec(
            "complete_delegated_task",
            "workspace.delegated_task.complete",
            side_effect=True,
        ),
    }
