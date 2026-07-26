package com.memoecho.connector.qqnapcat.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.memoecho.connector.qqnapcat.dto.AttachmentPayload;
import com.memoecho.connector.qqnapcat.dto.SenderPayload;
import com.memoecho.connector.qqnapcat.dto.UnifiedEventPayload;
import org.springframework.beans.factory.annotation.Autowired;
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
    private final OutboundMessageRegistry outboundMessageRegistry;

    @Autowired
    public NapcatEventMapper(OutboundMessageRegistry outboundMessageRegistry) {
        this.outboundMessageRegistry = outboundMessageRegistry;
    }

    /** 仅供不启动 Spring 容器的轻量单元测试使用。 */
    public NapcatEventMapper() {
        this(new OutboundMessageRegistry());
    }

    public UnifiedEventPayload map(JsonNode rawPayload) {
        // 这一层负责把 NapCat 原始事件抹平成平台无关事件，后面 event-center 和 Python runtime 就不用关心 QQ 字段差异。
        String postType = text(rawPayload, "post_type");
        String messageType = text(rawPayload, "message_type");
        String eventType = !postType.isBlank() ? postType : "unknown";
        String chatType = deriveChatType(postType, messageType);
        String chatId = deriveChatId(rawPayload, chatType, postType);
        SenderPayload sender = deriveSender(rawPayload);
        String eventText = deriveText(rawPayload);
        String platformMessageId = text(rawPayload, "message_id");
        OutboundMessageRegistry.OutboundMessage outboundMessage = outboundMessageRegistry.resolve(
                platformMessageId,
                chatType,
                chatId,
                normalizeText(eventText)
        ).orElse(null);

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
                eventText,
                attachments,
                mentions,
                deriveTimestamp(rawPayload),
                rawPayload,
                deriveActorType(postType, sender, rawPayload, outboundMessage),
                emptyToNull(platformMessageId),
                outboundMessage != null ? outboundMessage.clientMessageId() : null,
                outboundMessage != null ? outboundMessage.correlationId() : null,
                deriveSequence(rawPayload)
        );
    }

    /** 根据精确出站登记和平台账号身份确定消息参与者。 */
    private String deriveActorType(
            String postType,
            SenderPayload sender,
            JsonNode rawPayload,
            OutboundMessageRegistry.OutboundMessage outboundMessage
    ) {
        if (outboundMessage != null) {
            return "AGENT";
        }
        String selfId = firstNonBlank(text(rawPayload, "self_id"), text(rawPayload, "selfId"));
        if ("message_sent".equals(postType) || (!selfId.isBlank() && selfId.equals(sender.id()))) {
            return "OWNER";
        }
        if ("message".equals(postType)) {
            return "CONTACT";
        }
        return "SYSTEM";
    }

    /** 提取平台序号，供同一秒内的多条消息稳定排序。 */
    private Long deriveSequence(JsonNode rawPayload) {
        for (String field : List.of("message_seq", "real_seq", "message_id")) {
            String value = text(rawPayload, field);
            if (value.isBlank()) {
                continue;
            }
            try {
                return Long.parseLong(value);
            } catch (NumberFormatException ignored) {
                // 某些版本会返回非数字 real_seq，继续尝试下一字段。
            }
        }
        return null;
    }

    private String normalizeText(String value) {
        return value == null ? "" : value.replaceAll("\\s+", "").trim();
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
        JsonNode messageNode = rawPayload.path("message");
        if (!messageNode.isArray()) {
            return text(rawPayload, "raw_message");
        }

        StringBuilder builder = new StringBuilder();
        for (JsonNode segment : messageNode) {
            String type = text(segment, "type");
            JsonNode data = segment.path("data");
            String text = firstNonBlank(text(data, "text"), text(data, "content"), text(data, "file_name"));
            if (text.isBlank()) {
                text = describeNonTextSegment(type, data);
            }
            if (!text.isBlank()) {
                if (!builder.isEmpty()) {
                    builder.append('\n');
                }
                builder.append(text);
            }
        }
        return builder.isEmpty() ? text(rawPayload, "raw_message") : builder.toString();
    }

    /**
     * 为非文本消息生成可检索的语义占位符；原始字段仍完整保存在 rawPayload 和 segments 中。
     */
    private String describeNonTextSegment(String type, JsonNode data) {
        return switch (type) {
            case "image" -> firstNonBlank(text(data, "summary"), "[图片]");
            case "record" -> "[语音]";
            case "video" -> "[视频]";
            case "file" -> "[文件] " + firstNonBlank(text(data, "name"), text(data, "file"));
            case "reply" -> "[回复消息 " + text(data, "id") + "]";
            case "forward" -> "[合并转发]";
            case "face", "mface" -> "[表情]";
            case "json", "lightapp" -> "[卡片消息]";
            case "location" -> "[位置]";
            case "music" -> "[音乐分享]";
            case "contact" -> "[联系人分享]";
            case "dice" -> "[骰子]";
            case "rps" -> "[猜拳]";
            default -> "";
        };
    }

    private String deriveTimestamp(JsonNode rawPayload) {
        if (rawPayload.hasNonNull("time")) {
            long epochSeconds = rawPayload.path("time").asLong();
            return TIME_FORMATTER.format(Instant.ofEpochSecond(epochSeconds).atZone(SHANGHAI_ZONE));
        }
        return TIME_FORMATTER.format(Instant.now().atZone(SHANGHAI_ZONE));
    }

    private String deriveChatId(JsonNode rawPayload, String chatType, String postType) {
        if ("group".equals(chatType)) {
            return firstNonBlank(text(rawPayload, "group_id"), "unknown-group");
        }
        if ("private".equals(chatType) && "message_sent".equals(postType)) {
            // NapCat 的自身私聊回显中 user_id 是登录账号，真正联系人位于 target_id。
            // 若仍使用 user_id，本人消息会被写入“和自己聊天”的错误会话，历史上下文就会缺一半。
            return firstNonBlank(
                    text(rawPayload, "target_id"),
                    text(rawPayload.path("raw"), "peerUin"),
                    text(rawPayload, "user_id"),
                    "unknown-user"
            );
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
