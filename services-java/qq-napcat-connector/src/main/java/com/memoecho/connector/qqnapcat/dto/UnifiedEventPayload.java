package com.memoecho.connector.qqnapcat.dto;

import com.fasterxml.jackson.annotation.JsonInclude;
import com.fasterxml.jackson.databind.JsonNode;

import java.util.List;

@JsonInclude(JsonInclude.Include.NON_NULL)
public record UnifiedEventPayload(
        String eventId,
        String platform,
        String scene,
        String eventType,
        String chatType,
        String chatId,
        String selfId,
        SenderPayload sender,
        String text,
        List<AttachmentPayload> attachments,
        List<String> mentions,
        String timestamp,
        JsonNode rawPayload
) {
}
