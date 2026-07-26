package com.memoecho.eventcenter.model;

import java.time.Instant;

/** 保存一次 Agent Runtime 派发的可恢复状态。 */
public record AgentDispatchRetryJob(
        String eventId,
        String status,
        int attemptCount,
        Instant nextAttemptAt,
        String lastError,
        Instant createdAt,
        Instant updatedAt
) {
}
