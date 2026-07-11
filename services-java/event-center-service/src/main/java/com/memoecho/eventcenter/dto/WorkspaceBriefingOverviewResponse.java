package com.memoecho.eventcenter.dto;

public record WorkspaceBriefingOverviewResponse(
        String openingLine,
        String suggestedStart,
        Integer importantConversationCount,
        Integer pendingTaskCount,
        Integer todayScheduleCount,
        Integer actionRequiredCount
) {
}
