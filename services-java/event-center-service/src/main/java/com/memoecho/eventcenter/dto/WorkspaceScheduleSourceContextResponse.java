package com.memoecho.eventcenter.dto;

import java.util.List;

public record WorkspaceScheduleSourceContextResponse(
        String scheduleId,
        String scheduleTitle,
        String sourceType,
        String sourceEventId,
        String platform,
        String chatType,
        String chatId,
        String chatName,
        boolean sourceMessageFound,
        List<ConversationMessageResponse> messages
) {
}
