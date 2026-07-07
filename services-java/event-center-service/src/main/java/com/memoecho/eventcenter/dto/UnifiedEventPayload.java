package com.memoecho.eventcenter.dto;

import com.fasterxml.jackson.annotation.JsonInclude;
import com.fasterxml.jackson.databind.JsonNode;
import jakarta.validation.constraints.NotBlank;

import java.util.List;

@JsonInclude(JsonInclude.Include.NON_NULL)
public record UnifiedEventPayload(
        @NotBlank String eventId,
        @NotBlank String platform,
        String scene,
        @NotBlank String eventType,
        @NotBlank String chatType,
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
