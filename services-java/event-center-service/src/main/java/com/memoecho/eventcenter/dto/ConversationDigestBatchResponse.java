package com.memoecho.eventcenter.dto;

import java.time.Instant;
import java.util.List;

/** 消息空间展示的一次真实摘要批次。 */
public record ConversationDigestBatchResponse(
        String id,
        String platform,
        String chatType,
        String chatId,
        String aggregationKey,
        List<String> sourceEventIds,
        int messageCount,
        String summary,
        String happened,
        String actionItems,
        String nextStep,
        Instant periodStartedAt,
        Instant periodEndedAt,
        Instant generatedAt
) {
}
