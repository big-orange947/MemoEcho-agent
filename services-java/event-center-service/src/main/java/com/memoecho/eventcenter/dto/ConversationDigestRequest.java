package com.memoecho.eventcenter.dto;

import jakarta.validation.constraints.NotBlank;

import java.util.List;

/**
 * Python Runtime 在慢通道窗口到期后提交的会话摘要。
 */
public record ConversationDigestRequest(
        @NotBlank String platform,
        String scene,
        @NotBlank String chatType,
        String chatId,
        String selfId,
        @NotBlank String aggregationKey,
        List<String> sourceEventIds,
        Integer messageCount,
        @NotBlank String summary,
        String ownerUserId,
        java.time.Instant periodStartedAt,
        java.time.Instant periodEndedAt,
        String happened,
        String actionItems,
        String nextStep
) {
    /** 兼容尚未传递用户和时间范围的旧 Runtime。 */
    public ConversationDigestRequest(
            String platform, String scene, String chatType, String chatId, String selfId,
            String aggregationKey, List<String> sourceEventIds, Integer messageCount, String summary
    ) {
        this(platform, scene, chatType, chatId, selfId, aggregationKey, sourceEventIds,
                messageCount, summary, "default", null, null, "", "", "");
    }
}
