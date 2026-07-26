package com.memoecho.eventcenter.dto;

import java.util.List;

public record WorkspaceBriefingResponse(
        String generatedAt,
        Integer lookbackMinutes,
        WorkspaceBriefingOverviewResponse overview,
        List<WorkspaceConversationDigestResponse> importantConversations,
        List<WorkspaceTaskDigestResponse> pendingTasks,
        List<WorkspaceScheduleDigestResponse> todaySchedules,
        List<WorkspaceScheduleDigestResponse> upcomingSchedules,
        List<WorkspaceSuggestedActionResponse> suggestedActions
) {
}
