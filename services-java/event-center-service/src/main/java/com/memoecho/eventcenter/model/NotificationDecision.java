package com.memoecho.eventcenter.model;

/**
 * Agent 对单条消息给出的通知和归并结论。
 *
 * 该模型只保存可向工作台展示的决策字段，不能放入提示词、工具参数、用户密钥或原始外部响应。
 */
public record NotificationDecision(
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
