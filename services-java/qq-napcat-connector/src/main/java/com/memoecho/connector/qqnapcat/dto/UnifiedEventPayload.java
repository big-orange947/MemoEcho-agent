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
        JsonNode rawPayload,
        String actorType,
        String platformMessageId,
        String clientMessageId,
        String correlationId,
        Long sequence
) {
    /**
     * 保留旧构造方式，避免历史测试和其他连接器在协议升级时被迫同时修改。
     */
    public UnifiedEventPayload(
            String eventId, String platform, String scene, String eventType, String chatType,
            String chatId, String selfId, SenderPayload sender, String text,
            List<AttachmentPayload> attachments, List<String> mentions, String timestamp, JsonNode rawPayload
    ) {
        this(eventId, platform, scene, eventType, chatType, chatId, selfId, sender, text,
                attachments, mentions, timestamp, rawPayload, null, null, null, null, null);
    }
}
