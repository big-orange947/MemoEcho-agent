package com.memoecho.eventcenter.dto;

import java.util.List;

public record WorkspaceInboxResponse(
        String generatedAt,
        String inboxStatusFilter,
        Integer totalCount,
        Integer newCount,
        Integer readCount,
        Integer actionRequiredCount,
        List<WorkspaceInboxItemResponse> items
) {
}
