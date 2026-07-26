package com.memoecho.eventcenter.dto;

import java.time.Instant;
import java.util.List;

/** 向 Runtime 和客户端暴露当前会话任务状态，不包含模型内部推理过程。 */
public record ConversationProxyTaskStateResponse(
        String profileId,
        String profileName,
        String platform,
        String chatType,
        String chatId,
        String status,
        String completionSummary,
        String completionReason,
        List<String> completionEvidence,
        Instant requestedAt,
        Instant decidedAt,
        Instant updatedAt
) {
}
