package com.memoecho.connector.qqnapcat.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.memoecho.connector.qqnapcat.dto.AttachmentPayload;
import com.memoecho.connector.qqnapcat.dto.SenderPayload;
import com.memoecho.connector.qqnapcat.dto.UnifiedEventPayload;
import org.springframework.stereotype.Component;

import java.time.Instant;
import java.time.ZoneId;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;

@Component
public class NapcatEventMapper {

    private static final ZoneId SHANGHAI_ZONE = ZoneId.of("Asia/Shanghai");
    private static final DateTimeFormatter TIME_FORMATTER = DateTimeFormatter.ISO_OFFSET_DATE_TIME;

    public UnifiedEventPayload map(JsonNode rawPayload) {
        // 这一层负责把 NapCat 原始事件抹平成平台无关事件，后面 event-center 和 Python runtime 就不用关心 QQ 字段差异。
        String postType = text(rawPayload, "post_type");
        String messageType = text(rawPayload, "message_type");
        String eventType = !postType.isBlank() ? postType : "unknown";
        String chatType = deriveChatType(postType, messageType);
        String chatId = deriveChatId(rawPayload, chatType);
        SenderPayload sender = deriveSender(rawPayload);

        List<AttachmentPayload> attachments = new ArrayList<>();
        List<String> mentions = new ArrayList<>();
        collectSegments(rawPayload.path("message"), attachments, mentions);

        return new UnifiedEventPayload(
                deriveEventId(rawPayload, eventType, chatType),
                "qq",
                deriveScene(chatType),
                eventType,
                chatType,
                chatId,
                firstNonBlank(text(rawPayload, "self_id"), text(rawPayload, "selfId")),
                sender,
                deriveText(rawPayload),
                attachments,
                mentions,
                deriveTimestamp(rawPayload),
                rawPayload
        );
    }

    private void collectSegments(JsonNode messageNode, List<AttachmentPayload> attachments, List<String> mentions) {
        if (!messageNode.isArray()) {
            return;
        }

        for (JsonNode segment : messageNode) {
            String type = text(segment, "type");
            JsonNode data = segment.path("data");

            if ("at".equals(type)) {
                String qq = text(data, "qq");
                if (!qq.isBlank()) {
                    mentions.add(qq);
                }
            }

            if (isAttachmentType(type)) {
                // 附件先统一抽成轻量元数据，真正的下载或解析后面再由专门 Agent 处理。
                attachments.add(new AttachmentPayload(
                        firstNonBlank(text(data, "file_id"), text(data, "file")),
                        firstNonBlank(text(data, "file_name"), text(data, "name")),
                        type,
                        firstNonBlank(text(data, "url"), text(data, "file"))
                ));
            }
        }
    }

    private boolean isAttachmentType(String type) {
        return "file".equals(type) || "image".equals(type) || "record".equals(type) || "video".equals(type);
    }

    private SenderPayload deriveSender(JsonNode rawPayload) {
        JsonNode sender = rawPayload.path("sender");
        String senderId = firstNonBlank(text(sender, "user_id"), text(rawPayload, "user_id"));
        String senderName = firstNonBlank(text(sender, "nickname"), text(sender, "card"), "unknown");
        String senderRole = text(sender, "role");
        return new SenderPayload(senderId, senderName, emptyToNull(senderRole));
    }

    private String deriveText(JsonNode rawPayload) {
        String rawMessage = text(rawPayload, "raw_message");
        if (!rawMessage.isBlank()) {
            return rawMessage;
        }

        JsonNode messageNode = rawPayload.path("message");
        if (!messageNode.isArray()) {
            return "";
        }

        StringBuilder builder = new StringBuilder();
        for (JsonNode segment : messageNode) {
            JsonNode data = segment.path("data");
            String text = firstNonBlank(text(data, "text"), text(data, "content"), text(data, "file_name"));
            if (!text.isBlank()) {
                if (!builder.isEmpty()) {
                    builder.append('\n');
                }
                builder.append(text);
            }
        }
        return builder.toString();
    }

    private String deriveTimestamp(JsonNode rawPayload) {
        if (rawPayload.hasNonNull("time")) {
            long epochSeconds = rawPayload.path("time").asLong();
            return TIME_FORMATTER.format(Instant.ofEpochSecond(epochSeconds).atZone(SHANGHAI_ZONE));
        }
        return TIME_FORMATTER.format(Instant.now().atZone(SHANGHAI_ZONE));
    }

    private String deriveChatId(JsonNode rawPayload, String chatType) {
        if ("group".equals(chatType)) {
            return firstNonBlank(text(rawPayload, "group_id"), "unknown-group");
        }
        return firstNonBlank(text(rawPayload, "user_id"), "unknown-user");
    }

    private String deriveEventId(JsonNode rawPayload, String eventType, String chatType) {
        String messageId = text(rawPayload, "message_id");
        if (!messageId.isBlank()) {
            return "qq:" + eventType + ":" + chatType + ":" + messageId;
        }
        return "qq:" + eventType + ":" + chatType + ":" + UUID.randomUUID();
    }

    private String deriveChatType(String postType, String messageType) {
        if ("message".equals(postType) && "group".equals(messageType)) {
            return "group";
        }
        if ("message".equals(postType) && "private".equals(messageType)) {
            return "private";
        }
        return firstNonBlank(messageType, postType, "unknown");
    }

    private String deriveScene(String chatType) {
        if ("group".equals(chatType)) {
            return "life";
        }
        if ("private".equals(chatType)) {
            return "social";
        }
        return "general";
    }

    private String text(JsonNode node, String fieldName) {
        if (node == null || node.isMissingNode() || node.isNull()) {
            return "";
        }
        JsonNode value = node.path(fieldName);
        if (value.isMissingNode() || value.isNull()) {
            return "";
        }
        return value.asText("");
    }

    private String firstNonBlank(String... values) {
        for (String value : values) {
            if (value != null && !value.isBlank()) {
                return value;
            }
        }
        return "";
    }

    private String emptyToNull(String value) {
        return value == null || value.isBlank() ? null : value;
    }
}
