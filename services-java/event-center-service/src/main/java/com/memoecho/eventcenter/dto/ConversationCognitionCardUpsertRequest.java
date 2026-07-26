package com.memoecho.eventcenter.dto;

import com.memoecho.eventcenter.model.ConversationCognitionCard;
import jakarta.validation.constraints.NotBlank;

import java.util.List;

/** 用户修正或 Runtime 推断一张会话认知卡时使用的统一请求。 */
public record ConversationCognitionCardUpsertRequest(
        @NotBlank String platform,
        @NotBlank String chatType,
        @NotBlank String chatId,
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
        Integer sourceMessageCount
) {
}
