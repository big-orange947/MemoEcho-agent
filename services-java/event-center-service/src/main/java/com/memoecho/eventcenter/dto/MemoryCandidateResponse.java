package com.memoecho.eventcenter.dto;

import java.time.Instant;
import java.util.List;

/** 返回给桌面端或 Runtime 的长期记忆视图，不包含任何原始聊天正文。 */
public record MemoryCandidateResponse(
        String id,
        String subject,
        String predicate,
        String value,
        String scopeType,
        String platform,
        String scene,
        String chatType,
        String chatId,
        List<String> sourceEventIds,
        String sourceActorType,
        String factAuthority,
        double confidence,
        String status,
        String rejectionReason,
        Instant firstSeenAt,
        Instant lastSeenAt,
        Instant expiresAt,
        Instant createdAt,
        Instant updatedAt
) {
}
