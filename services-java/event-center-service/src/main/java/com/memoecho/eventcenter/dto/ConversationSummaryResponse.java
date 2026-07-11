package com.memoecho.eventcenter.dto;

public record ConversationSummaryResponse(
        String platform,
        String chatType,
        String chatId,
        String chatName,
        String lastSenderName,
        String lastMessage,
        String lastMessageTime,
        String lastRoute,
        String lastDispatchMode,
        String lastProcessingStatus,
        String lastWriteBackStatus,
        boolean actionRequired,
        Integer unreadLikeCount,
        Integer urgentCount,
        boolean autoReplyEnabled,
        boolean summaryEnabled
) {
}
