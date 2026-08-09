package com.memoecho.eventcenter.model;

import java.time.Instant;

/** 表示一次工作流步骤激活对应的可靠 Runtime 投递。 */
public record DelegatedWorkflowStepDispatch(
        long id,
        String workflowId,
        String stepKey,
        long activationVersion,
        String taskId,
        String userId,
        String status,
        int attemptCount,
        Instant nextAttemptAt,
        Instant leaseUntil,
        String lastError
) {
    /** 返回跨重试保持不变的幂等键，Runtime 可据此避免重复执行步骤。 */
    public String idempotencyKey() {
        return workflowId + ":" + stepKey + ":" + activationVersion;
    }
}
