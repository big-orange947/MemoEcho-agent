package com.memoecho.eventcenter.dto;

public record WorkspaceConversationDigestResponse(
        String platform,
        String chatType,
        String chatId,
        String chatName,
        String lastSenderName,
        String lastMessage,
        String lastMessageTime,
        String dispatchMode,
        String highlightReason,
        String processingStatus,
        String writeBackStatus,
        boolean actionRequired
) {
}
