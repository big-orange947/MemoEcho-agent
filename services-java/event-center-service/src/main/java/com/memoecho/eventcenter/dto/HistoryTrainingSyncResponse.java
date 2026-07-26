package com.memoecho.eventcenter.dto;

/** 一次私聊历史训练样本同步的统计结果。 */
public record HistoryTrainingSyncResponse(
        String profileId,
        int requestedConversations,
        int importedMessages,
        int duplicateMessages,
        int skippedMessages,
        boolean personalSkillAvailable,
        String personalSkillReference,
        int eligibleSampleCount,
        double confidence
) {
}
