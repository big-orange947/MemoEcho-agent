package com.memoecho.eventcenter.dto;

import com.memoecho.eventcenter.model.ConversationCognitionCard;

import java.time.Instant;
import java.util.List;

/** 返回给桌面端或 Agent Runtime 的会话认知卡。 */
public record ConversationCognitionCardResponse(
        String id,
        String platform,
        String chatType,
        String chatId,
        int version,
        ConversationCognitionCard.CognitionField relationship,
        ConversationCognitionCard.CognitionField preferredAddress,
        ConversationCognitionCard.CognitionField counterpartyTraits,
        ConversationCognitionCard.CognitionField ownerExpressionHabits,
        ConversationCognitionCard.CognitionField counterpartyExpressionHabits,
        ConversationCognitionCard.CognitionField backgroundSummary,
        ConversationCognitionCard.CognitionField currentProgress,
        List<String> knownFacts,
        List<String> recentTopics,
        List<String> openQuestions,
        List<String> sourceEventIds,
        int sourceMessageCount,
        String status,
        Instant analyzedAt,
        Instant createdAt,
        Instant updatedAt
) {
}
