package com.memoecho.eventcenter.dto;

import com.fasterxml.jackson.annotation.JsonInclude;
import com.fasterxml.jackson.annotation.JsonIgnore;
import com.fasterxml.jackson.databind.JsonNode;
import jakarta.validation.constraints.NotBlank;

import java.util.List;
import java.util.ArrayList;

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
        JsonNode rawPayload,
        String actorType,
        String platformMessageId,
        String clientMessageId,
        String correlationId,
        Long sequence,
        String sentAt,
        String receivedAt,
        String importedAt,
        String direction,
        String delegatedTaskId
) {
    /**
     * 兼容尚未提供参与者身份和关联 ID 的旧事件、导入任务与单元测试。
     */
    public UnifiedEventPayload(
            String eventId, String platform, String scene, String eventType, String chatType,
            String chatId, String selfId, SenderPayload sender, String text,
            List<AttachmentPayload> attachments, List<String> mentions, String timestamp, JsonNode rawPayload
    ) {
        this(eventId, platform, scene, eventType, chatType, chatId, selfId, sender, text,
                attachments, mentions, timestamp, rawPayload, null, null, null, null, null,
                timestamp, null, null, null, null);
    }

    /** 兼容已经提供统一消息身份、但尚未拆分多种时间语义的连接器。 */
    public UnifiedEventPayload(
            String eventId, String platform, String scene, String eventType, String chatType,
            String chatId, String selfId, SenderPayload sender, String text,
            List<AttachmentPayload> attachments, List<String> mentions, String timestamp, JsonNode rawPayload,
            String actorType, String platformMessageId, String clientMessageId, String correlationId, Long sequence
    ) {
        this(eventId, platform, scene, eventType, chatType, chatId, selfId, sender, text,
                attachments, mentions, timestamp, rawPayload, actorType, platformMessageId,
                clientMessageId, correlationId, sequence, timestamp, null, null, null, null);
    }

    /**
     * 从原始平台事件派生消息段，供 Python Agent 统一处理图片、语音、回复和转发等内容。
     * 该字段不参与构造和数据库迁移，因此旧事件也可以在读取时获得消息段。
     */
    // segments 是从 rawPayload.message 实时派生的只读字段，不能在回读历史 JSON 时交给 Jackson 写入。
    @JsonIgnore
    public List<JsonNode> segments() {
        if (rawPayload == null || !rawPayload.path("message").isArray()) {
            return List.of();
        }
        List<JsonNode> result = new ArrayList<>();
        rawPayload.path("message").elements().forEachRemaining(result::add);
        return List.copyOf(result);
    }
}
