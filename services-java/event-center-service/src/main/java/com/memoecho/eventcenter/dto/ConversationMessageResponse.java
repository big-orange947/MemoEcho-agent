package com.memoecho.eventcenter.dto;

import java.util.List;

public record ConversationMessageResponse(
        String eventId,
        String platform,
        String chatType,
        String chatId,
        String chatName,
        String senderId,
        String senderName,
        String senderRole,
        String text,
        String timestamp,
        List<String> mentions,
        List<AttachmentPayload> attachments,
        boolean processed,
        boolean replied,
        String route,
        String dispatchMode
) {
}
