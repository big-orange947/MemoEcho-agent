from __future__ import annotations

from pydantic import BaseModel, Field


class DelegatedWorkflowPlanStep(BaseModel):
    """描述主控台委托工作流中的一个可执行步骤。"""

    step_key: str = Field(alias="stepKey")
    order: int
    role: str = "executor"
    instruction: str
    target_chat_type: str = Field(alias="targetChatType")
    target_chat_id: str = Field(alias="targetChatId")
    depends_on: list[str] = Field(default_factory=list, alias="dependsOn")
    required_facts: list[str] = Field(default_factory=list, alias="requiredFacts")
    produces_facts: list[str] = Field(default_factory=list, alias="producesFacts")

    model_config = {"populate_by_name": True}


class DelegatedWorkflowPlan(BaseModel):
    """承载 RouterAgent 生成并通过结构校验的父工作流计划。"""

    title: str
    workflow_type: str = Field(default="PLAN_EXECUTE", alias="workflowType")
    steps: list[DelegatedWorkflowPlanStep]

    model_config = {"populate_by_name": True}


class DelegatedWorkflowStepExecutionRequest(BaseModel):
    """承载 Java outbox 投递的工作流步骤执行请求。"""

    workflow_id: str = Field(alias="workflowId")
    step_key: str = Field(alias="stepKey")
    activation_version: int = Field(alias="activationVersion")
    task_id: str = Field(alias="taskId")
    user_id: str = Field(alias="userId")
    idempotency_key: str = Field(alias="idempotencyKey")

    model_config = {"populate_by_name": True}


class DelegatedWorkflowStepExecutionResponse(BaseModel):
    """描述步骤是否真正生效，供 Java outbox 决定确认消费或稍后重试。"""

    status: str
    reason: str = ""
    workflow_id: str = Field(alias="workflowId")
    step_key: str = Field(alias="stepKey")
    retryable: bool = False
    write_back_actions: list[str] = Field(default_factory=list, alias="writeBackActions")

    model_config = {"populate_by_name": True}
