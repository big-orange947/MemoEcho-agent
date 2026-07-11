package com.memoecho.eventcenter.dto;

/**
 * 工作台使用的通知决策响应模型。
 */
public record NotificationDecisionResponse(
        String channel,
        String priority,
        String triggerReason,
        boolean notifyNow,
        String aggregationKey,
        String aggregationStatus,
        int bufferedCount,
        String summaryCandidate
) {
}
