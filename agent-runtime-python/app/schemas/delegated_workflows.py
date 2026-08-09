from __future__ import annotations

from pydantic import BaseModel, Field


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
    """描述步骤是否真正执行；过期 outbox 消息返回 ignored。"""

    status: str
    reason: str = ""
    workflow_id: str = Field(alias="workflowId")
    step_key: str = Field(alias="stepKey")

    model_config = {"populate_by_name": True}
