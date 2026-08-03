from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ConversationCandidate(BaseModel):
    """描述 Java 已授权给当前用户访问的一条候选会话。"""

    platform: str
    chat_type: str = Field(alias="chatType")
    chat_id: str = Field(alias="chatId")
    chat_name: str = Field(default="", alias="chatName")
    # 通讯录备注、昵称和群名等可检索别名；缺失时保持兼容旧版 Java 响应。
    aliases: list[str] = Field(default_factory=list, alias="aliases")
    last_sender_name: str = Field(default="", alias="lastSenderName")
    last_message: str = Field(default="", alias="lastMessage")
    last_message_time: str = Field(default="", alias="lastMessageTime")
    last_route: str = Field(default="", alias="lastRoute")
    last_dispatch_mode: str = Field(default="", alias="lastDispatchMode")
    last_processing_status: str = Field(default="", alias="lastProcessingStatus")
    last_write_back_status: str = Field(default="", alias="lastWriteBackStatus")
    action_required: bool = Field(default=False, alias="actionRequired")
    unread_like_count: int | None = Field(default=None, alias="unreadLikeCount")
    urgent_count: int | None = Field(default=None, alias="urgentCount")
    auto_reply_enabled: bool = Field(default=False, alias="autoReplyEnabled")
    summary_enabled: bool = Field(default=False, alias="summaryEnabled")

    model_config = {"populate_by_name": True}


class DelegatedTaskCompileRequest(BaseModel):
    """接收工作台自然语言命令和服务端筛选过的会话白名单。"""

    user_id: str = Field(alias="userId")
    command: str
    conversations: list[ConversationCandidate] = Field(default_factory=list)
    # 这个字段只由 Python 主控台路由器写入。为 true 时表示 conversations 已经是路由器
    # 从授权候选里明确选出的目标，编译图可以直接绑定；普通外部请求不能依赖单候选自动命中。
    target_resolved_by_router: bool = Field(default=False, alias="targetResolvedByRouter")

    model_config = {"populate_by_name": True}


class DelegatedTaskCompileResponse(BaseModel):
    """保持与 Java DelegatedTaskCompilationResponse 完全一致的任务契约。"""

    recognized: bool = False
    task_type: str = Field(default="CONVERSATION_GOAL", alias="taskType")
    target_query: str = Field(default="", alias="targetQuery")
    platform: str = ""
    chat_type: str = Field(default="", alias="chatType")
    chat_id: str = Field(default="", alias="chatId")
    target_name: str = Field(default="", alias="targetName")
    objective: str = ""
    success_criteria: str = Field(default="", alias="successCriteria")
    deadline_text: str = Field(default="", alias="deadlineText")
    confidence: float = 0.0
    clarification_question: str = Field(default="", alias="clarificationQuestion")
    requires_confirmation: bool = Field(default=False, alias="requiresConfirmation")
    execution_mode: str = Field(default="AUTO_COMPLETE", alias="executionMode")
    initial_progress: str = Field(default="", alias="initialProgress")
    state_json: str = Field(default="{}", alias="stateJson")

    model_config = {"populate_by_name": True}


class DelegatedTaskRuntimeDecision(BaseModel):
    """描述 LangGraph 对一轮委托执行结果给出的可持久化状态更新。"""

    status: str = "ACTIVE"
    progress_summary: str = Field(default="", alias="progressSummary")
    state_json: str = Field(default="{}", alias="stateJson")
    last_event_id: str = Field(default="", alias="lastEventId")
    completion_report: str = Field(default="", alias="completionReport")
    evidence: list[str] = Field(default_factory=list)
    requested_tool: str = Field(default="update_delegated_task", alias="requestedTool")
    tool_arguments: dict[str, Any] = Field(default_factory=dict, alias="toolArguments")

    model_config = {"populate_by_name": True}


class DelegatedTaskRuntimeInput(BaseModel):
    """封装运行状态图需要的持久化任务、带时间戳历史和当前执行结果。"""

    task: dict[str, Any]
    history: list[dict[str, Any]] = Field(default_factory=list)
    pre_task_history: list[dict[str, Any]] = Field(default_factory=list, alias="preTaskHistory")
    history_access_allowed: bool = Field(default=True, alias="historyAccessAllowed")
    event: dict[str, Any]
    final_reply: str = Field(default="", alias="finalReply")
    write_back_actions: list[str] = Field(default_factory=list, alias="writeBackActions")

    model_config = {"populate_by_name": True}


class DelegatedTaskActionInput(BaseModel):
    """封装委托任务在生成回复前进行动作决策所需的任务、历史和当前事件。"""

    task: dict[str, Any]
    history: list[dict[str, Any]] = Field(default_factory=list)
    pre_task_history: list[dict[str, Any]] = Field(default_factory=list, alias="preTaskHistory")
    history_access_allowed: bool = Field(default=True, alias="historyAccessAllowed")
    event: dict[str, Any]

    model_config = {"populate_by_name": True}


class DelegatedTaskActionDecision(BaseModel):
    """描述委托图本轮选择的动作，编排器只允许执行这里声明的受控工具。"""

    action: str = "WAIT"
    reason: str = ""
    progress_summary: str = Field(default="", alias="progressSummary")
    message_instruction: str = Field(default="", alias="messageInstruction")
    state_json: str = Field(default="{}", alias="stateJson")
    last_event_id: str = Field(default="", alias="lastEventId")
    completion_report: str = Field(default="", alias="completionReport")
    evidence: list[str] = Field(default_factory=list)
    requested_tool: str = Field(default="update_delegated_task", alias="requestedTool")
    tool_arguments: dict[str, Any] = Field(default_factory=dict, alias="toolArguments")

    model_config = {"populate_by_name": True}
