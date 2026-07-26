package com.memoecho.eventcenter.model;

import java.time.Instant;

/**
 * 一条可追溯的长期记忆候选。
 *
 * <p>候选事实只有进入 VERIFIED 状态后才能提供给 Runtime。sourceEventIdsJson 保存证据事件，
 * 便于用户确认、纠错和后续失效处理，不能用 Agent 自己生成的文本充当真人事实。</p>
 */
public record MemoryCandidate(
        String id,
        String userId,
        String subject,
        String predicate,
        String value,
        String scopeType,
        String platform,
        String scene,
        String chatType,
        String chatId,
        String sourceEventIdsJson,
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
