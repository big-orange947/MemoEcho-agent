package com.memoecho.eventcenter.dto;

/**
 * 请求 Agent Runtime 执行一个已经由数据库激活的工作流步骤。
 * activationVersion 与幂等键共同区分同一步骤的不同激活轮次。
 */
public record DelegatedWorkflowStepExecutionRequest(
        String workflowId,
        String stepKey,
        long activationVersion,
        String taskId,
        String userId,
        String idempotencyKey
) {
}
