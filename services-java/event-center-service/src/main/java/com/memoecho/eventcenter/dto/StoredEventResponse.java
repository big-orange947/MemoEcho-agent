package com.memoecho.eventcenter.dto;

public record StoredEventResponse(
        String eventId,
        String platform,
        String eventType,
        String chatType,
        String chatId,
        String text,
        String timestamp,
        String receivedAt
) {
}
