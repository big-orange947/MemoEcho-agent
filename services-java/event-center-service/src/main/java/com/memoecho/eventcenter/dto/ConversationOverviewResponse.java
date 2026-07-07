package com.memoecho.eventcenter.dto;

public record ConversationOverviewResponse(
        Integer totalConversations,
        Integer privateConversations,
        Integer groupConversations,
        Integer urgentConversations,
        Integer summaryEnabledConversations,
        Integer activeInLastHourConversations
) {
}
