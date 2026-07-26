package com.memoecho.eventcenter.model;

import java.time.Instant;
import java.util.List;

/** 保存某个设定在具体会话中的任务生命周期，避免客户端或 Runtime 重启后重复执行任务。 */
public record ConversationProxyTaskState(
        String profileId,
        String userId,
        String platform,
        String chatType,
        String chatId,
        String objectiveHash,
        String status,
        String completionSummary,
        String completionReason,
        List<String> completionEvidence,
        Instant requestedAt,
        Instant decidedAt,
        Instant createdAt,
        Instant updatedAt
) {
}
