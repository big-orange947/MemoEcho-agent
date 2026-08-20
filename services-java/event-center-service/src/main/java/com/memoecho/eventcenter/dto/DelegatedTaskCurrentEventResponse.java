package com.memoecho.eventcenter.dto;

import java.time.Instant;

/**
 * Runtime 读取的步骤当前事件（L0）视图。
 * payloadJson 保留完整入站事件 JSON，供历史接口失败时继续推理。
 */
public record DelegatedTaskCurrentEventResponse(
        String taskId,
        String workflowId,
        String stepKey,
        String conversationScopeJson,
        String eventId,
        String eventType,
        String senderId,
        String text,
        Instant occurredAt,
        String payloadJson
) {
}
