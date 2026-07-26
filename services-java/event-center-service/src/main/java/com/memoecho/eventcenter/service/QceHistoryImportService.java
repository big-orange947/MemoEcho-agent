package com.memoecho.eventcenter.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.JsonNodeFactory;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.memoecho.eventcenter.dto.AttachmentPayload;
import com.memoecho.eventcenter.dto.QceImportMessagePreview;
import com.memoecho.eventcenter.dto.QceImportPreviewResponse;
import com.memoecho.eventcenter.dto.QceImportRequest;
import com.memoecho.eventcenter.dto.QceImportResponse;
import com.memoecho.eventcenter.dto.SenderPayload;
import com.memoecho.eventcenter.dto.UnifiedEventPayload;
import com.memoecho.eventcenter.model.StoredEvent;
import com.memoecho.eventcenter.repository.EventRecordRepository;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.web.server.ResponseStatusException;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Instant;
import java.time.format.DateTimeParseException;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

/**
 * 将 QQ Chat Exporter (QCE) 的单文件 JSON 转换为 Memo Echo 统一历史事件。
 *
 * <p>该服务刻意不复用 {@link EventCenterApplicationService#ingest(UnifiedEventPayload)}：
 * QCE 数据是用户主动导入的旧记录，只能作为上下文和检索素材，绝不能再次触发 Agent 自动回复。</p>
 */
@Service
public class QceHistoryImportService {

    private static final int PREVIEW_SAMPLE_LIMIT = 5;

    private final EventRecordRepository repository;

    public QceHistoryImportService(EventRecordRepository repository) {
        this.repository = repository;
    }

    /**
     * 解析 QCE 单文件 JSON 并返回统计结果，不会写入数据库。
     */
    public QceImportPreviewResponse preview(QceImportRequest request) {
        ParsedExport parsed = parse(request);
        List<QceImportMessagePreview> samples = parsed.messages().stream()
                .limit(PREVIEW_SAMPLE_LIMIT)
                .map(message -> new QceImportMessagePreview(
                        message.sourceId(),
                        message.sender().name(),
                        shorten(message.text()),
                        message.timestamp().toString(),
                        message.attachments().size()
                ))
                .toList();

        int imageAttachments = countAttachments(parsed.messages(), "image");
        int videoAttachments = countAttachments(parsed.messages(), "video");
        int audioAttachments = countAttachments(parsed.messages(), "audio");
        int fileAttachments = countAttachments(parsed.messages(), "file");
        int attachmentMessages = (int) parsed.messages().stream()
                .filter(message -> !message.attachments().isEmpty())
                .count();
        int textMessages = (int) parsed.messages().stream()
                .filter(message -> !message.text().isBlank())
                .count();
        List<String> warnings = new ArrayList<>();
        if (parsed.chatId().isBlank()) {
            warnings.add("QCE 群聊导出通常不包含群号；请在导入时选择或填写对应的 QQ 会话。 ");
        }
        if (parsed.messages().isEmpty()) {
            warnings.add("导出文件中没有可导入的 messages 记录。");
        }
        if (attachmentMessages > 0) {
            warnings.add("图片、视频、语音和文件只会保存元数据及本地资源引用，不会自动上传或调用模型分析。");
        }

        return new QceImportPreviewResponse(
                parsed.chatName(),
                parsed.chatType(),
                parsed.chatId(),
                parsed.selfId(),
                parsed.chatId().isBlank(),
                parsed.messages().size(),
                textMessages,
                attachmentMessages,
                imageAttachments,
                videoAttachments,
                audioAttachments,
                fileAttachments,
                parsed.messages().isEmpty() ? null : parsed.messages().getFirst().timestamp().toString(),
                parsed.messages().isEmpty() ? null : parsed.messages().getLast().timestamp().toString(),
                samples,
                List.copyOf(warnings)
        );
    }

    /**
     * 导入历史记录并按确定性事件 ID 去重；整个过程不会派发 Python Runtime。
     */
    public QceImportResponse importHistory(String ownerUserId, QceImportRequest request) {
        ParsedExport parsed = parse(request);
        if (parsed.chatId().isBlank()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST,
                    "无法从 QCE 文件识别会话 ID；群聊请在客户端选择对应群聊后再导入。");
        }

        int importedCount = 0;
        int duplicateCount = 0;
        int attachmentCount = 0;
        for (ParsedMessage message : parsed.messages()) {
            String eventId = stableEventId(parsed, message);
            if (repository.exists(eventId)) {
                duplicateCount++;
                continue;
            }
            attachmentCount += message.attachments().size();
            UnifiedEventPayload payload = toUnifiedEvent(eventId, parsed, message, request.sourceName());
            StoredEvent imported = StoredEvent.received(eventId, ownerUserId, payload, message.timestamp())
                    .markProcessed(
                            "IMPORTED_HISTORY",
                            "已从 QQ Chat Exporter 导入历史消息，不会触发自动回复。",
                            "history_import",
                            "SKIPPED",
                            false,
                            message.timestamp(),
                            "",
                            null
                    )
                    // 历史导入不能出现在待办收件箱，也不应产生通知红点。
                    .markInboxStatus("DONE", null, message.timestamp())
                    .withMessageOrigin("HISTORY_IMPORT");
            repository.save(imported);
            importedCount++;
        }
        return new QceImportResponse(
                parsed.chatId(),
                parsed.chatType(),
                importedCount,
                duplicateCount,
                attachmentCount,
                "历史记录已导入。导入内容仅用于上下文、检索和用户显式授权的风格训练。"
        );
    }

    /**
     * 将 QCE 的导出文档标准化为内部结构；第一版只接受官方单文件 JSON，避免静默误读 HTML 或 TXT。
     */
    private ParsedExport parse(QceImportRequest request) {
        if (request == null || request.exportData() == null || !request.exportData().isObject()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "请提供 QCE 导出的单文件 JSON 对象。");
        }
        JsonNode document = request.exportData();
        JsonNode chatInfo = document.path("chatInfo");
        JsonNode messages = document.path("messages");
        if (!chatInfo.isObject() || !messages.isArray()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST,
                    "不是受支持的 QCE 单文件 JSON：缺少 chatInfo 或 messages 字段。");
        }

        String detectedChatType = normalizeChatType(firstNonBlank(
                request.chatTypeOverride(), chatInfo.path("type").asText(), chatInfo.path("chatType").asText()));
        String chatId = firstNonBlank(
                request.chatIdOverride(),
                chatInfo.path("peerUin").asText(),
                chatInfo.path("peerUid").asText(),
                chatInfo.path("chatId").asText()
        );
        String chatName = firstNonBlank(chatInfo.path("name").asText(), chatId, "QQ 历史会话");
        String selfId = firstNonBlank(chatInfo.path("selfUin").asText(), chatInfo.path("selfUid").asText());
        List<ParsedMessage> parsedMessages = new ArrayList<>();
        int index = 0;
        for (JsonNode message : messages) {
            ParsedMessage parsedMessage = parseMessage(message, index++);
            if (parsedMessage != null) {
                parsedMessages.add(parsedMessage);
            }
        }
        parsedMessages.sort(java.util.Comparator.comparing(ParsedMessage::timestamp));
        String resolvedSelfId = inferPrivateSelfId(detectedChatType, chatId, selfId, parsedMessages);
        return new ParsedExport(chatName, detectedChatType, chatId, resolvedSelfId, List.copyOf(parsedMessages));
    }

    /**
     * 一些 QCE 版本不导出 selfUin。私聊的 chatId 一定是对方账号，因此可用另一位发送者补全本人 ID。
     * 群聊存在多名发送者，无法安全推断时保持为空，绝不把任意群成员误认成当前用户。
     */
    private String inferPrivateSelfId(
            String chatType,
            String chatId,
            String declaredSelfId,
            List<ParsedMessage> messages
    ) {
        if (declaredSelfId != null && !declaredSelfId.isBlank()) {
            return declaredSelfId;
        }
        if (!"private".equals(chatType) || chatId == null || chatId.isBlank()) {
            return "";
        }
        return messages.stream()
                .map(message -> message.sender().id())
                .filter(senderId -> senderId != null && !senderId.isBlank())
                .filter(senderId -> !"unknown".equals(senderId))
                .filter(senderId -> !chatId.equals(senderId))
                .findFirst()
                .orElse("");
    }

    /**
     * 提取一条 QCE CleanMessage 的文本、发送者、@ 信息与非文本附件。
     */
    private ParsedMessage parseMessage(JsonNode message, int index) {
        if (message == null || !message.isObject()) {
            return null;
        }
        JsonNode senderNode = message.path("sender");
        String senderId = firstNonBlank(senderNode.path("uin").asText(), senderNode.path("uid").asText(), "unknown");
        String senderName = firstNonBlank(
                senderNode.path("groupCard").asText(), senderNode.path("remark").asText(),
                senderNode.path("name").asText(), senderNode.path("nickname").asText(), senderId
        );
        SenderPayload sender = new SenderPayload(senderId, senderName, senderNode.path("title").asText("member"));
        JsonNode content = message.path("content");
        String text = content.path("text").asText("").trim();
        List<String> mentions = new ArrayList<>();
        List<AttachmentPayload> attachments = new ArrayList<>();
        JsonNode elements = content.path("elements");
        if (elements.isArray()) {
            int elementIndex = 0;
            for (JsonNode element : elements) {
                String type = element.path("type").asText(element.path("elementType").asText()).trim().toLowerCase(Locale.ROOT);
                JsonNode data = element.path("data");
                if ("at".equals(type)) {
                    String mention = firstNonBlank(data.path("uin").asText(), data.path("uid").asText(), data.path("name").asText());
                    if (!mention.isBlank()) {
                        mentions.add(mention);
                    }
                }
                AttachmentPayload attachment = toAttachment(data, type, message.path("id").asText("row-" + index), elementIndex++);
                if (attachment != null) {
                    attachments.add(attachment);
                }
            }
        }
        JsonNode resources = content.path("resources");
        if (resources.isArray()) {
            int resourceIndex = 0;
            for (JsonNode resource : resources) {
                AttachmentPayload attachment = toAttachment(
                        resource,
                        resource.path("resourceType").asText(resource.path("type").asText()),
                        message.path("id").asText("row-" + index),
                        10_000 + resourceIndex++
                );
                if (attachment != null && attachments.stream().noneMatch(existing -> sameAttachment(existing, attachment))) {
                    attachments.add(attachment);
                }
            }
        }
        String sourceId = firstNonBlank(message.path("id").asText(), message.path("seq").asText(), "row-" + index);
        return new ParsedMessage(sourceId, sender, text, parseTimestamp(message), List.copyOf(attachments), List.copyOf(mentions), message.deepCopy());
    }

    /**
     * QCE 使用毫秒时间戳；同时兼容秒级时间戳与 ISO 时间字符串，防止不同版本导出文件被误排到 1970 年。
     */
    private Instant parseTimestamp(JsonNode message) {
        JsonNode timestamp = message.path("timestamp");
        if (timestamp.canConvertToLong()) {
            long value = timestamp.asLong();
            return value < 100_000_000_000L ? Instant.ofEpochSecond(value) : Instant.ofEpochMilli(value);
        }
        String iso = timestamp.asText("");
        if (!iso.isBlank()) {
            try {
                return Instant.parse(iso);
            } catch (DateTimeParseException ignored) {
                // QCE 还有可读 time 字段，但它没有时区，无法可靠还原；退回当前时间更安全。
            }
        }
        return Instant.now();
    }

    /**
     * 只把可定位的媒体元素视为附件。表情、回复、转发和 JSON 卡片仍会保留在 rawPayload 中供后续扩展解析。
     */
    private AttachmentPayload toAttachment(JsonNode data, String rawType, String messageId, int index) {
        String fileType = normalizeAttachmentType(rawType);
        if (fileType == null) {
            return null;
        }
        String fileName = firstNonBlank(data.path("fileName").asText(), data.path("filename").asText(), data.path("name").asText());
        String url = firstNonBlank(
                data.path("localPath").asText(), data.path("url").asText(), data.path("path").asText(), data.path("filePath").asText()
        );
        String fileId = firstNonBlank(data.path("fileId").asText(), data.path("md5").asText(), messageId + ":" + fileType + ":" + index);
        return new AttachmentPayload(fileId, fileName, fileType, url);
    }

    /**
     * 转换为现有统一事件；rawPayload 仍保留 QCE 原始结构，方便将来补充转发、卡片和表情解析。
     */
    private UnifiedEventPayload toUnifiedEvent(
            String eventId,
            ParsedExport export,
            ParsedMessage message,
            String sourceName
    ) {
        ObjectNode rawPayload = JsonNodeFactory.instance.objectNode();
        rawPayload.put("source", "qq-chat-exporter");
        rawPayload.put("messageOrigin", "HISTORY_IMPORT");
        rawPayload.put("historyImport", true);
        rawPayload.put("chatName", export.chatName());
        rawPayload.put("sourceName", sourceName == null ? "" : sourceName);
        rawPayload.set("qceMessage", message.rawMessage());
        return new UnifiedEventPayload(
                eventId,
                "qq",
                "history",
                "message",
                export.chatType(),
                export.chatId(),
                export.selfId(),
                message.sender(),
                message.text(),
                message.attachments(),
                message.mentions(),
                message.timestamp().toString(),
                rawPayload
        );
    }

    /**
     * 用会话与源消息 ID 生成稳定事件 ID，使重复选择同一导出文件也不会产生重复历史记录。
     */
    private String stableEventId(ParsedExport export, ParsedMessage message) {
        String material = String.join("|", "qce", export.chatType(), export.chatId(), message.sourceId(),
                message.sender().id(), message.timestamp().toString(), message.text());
        return "qce:" + sha256(material);
    }

    private int countAttachments(List<ParsedMessage> messages, String fileType) {
        return (int) messages.stream()
                .flatMap(message -> message.attachments().stream())
                .filter(attachment -> fileType.equals(attachment.fileType()))
                .count();
    }

    private boolean sameAttachment(AttachmentPayload left, AttachmentPayload right) {
        return left.fileType().equals(right.fileType())
                && left.fileName().equals(right.fileName())
                && left.url().equals(right.url());
    }

    private String normalizeAttachmentType(String type) {
        return switch (type == null ? "" : type.trim().toLowerCase(Locale.ROOT)) {
            case "image", "pic" -> "image";
            case "video" -> "video";
            case "audio", "voice", "ptt", "record" -> "audio";
            case "file" -> "file";
            default -> null;
        };
    }

    private String normalizeChatType(String rawType) {
        String normalized = rawType == null ? "" : rawType.trim().toLowerCase(Locale.ROOT);
        return switch (normalized) {
            case "private", "c2c", "friend" -> "private";
            case "group", "group_chat" -> "group";
            default -> "private";
        };
    }

    private String firstNonBlank(String... values) {
        for (String value : values) {
            if (value != null && !value.isBlank()) {
                return value.trim();
            }
        }
        return "";
    }

    private String shorten(String text) {
        if (text == null || text.isBlank()) {
            return "[非文本消息]";
        }
        return text.length() <= 80 ? text : text.substring(0, 80) + "...";
    }

    private String sha256(String material) {
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256")
                    .digest(material.getBytes(StandardCharsets.UTF_8));
            StringBuilder result = new StringBuilder(64);
            for (byte value : digest) {
                result.append(String.format("%02x", value));
            }
            return result.toString();
        } catch (NoSuchAlgorithmException exception) {
            throw new IllegalStateException("当前 JDK 不支持 SHA-256。", exception);
        }
    }

    private record ParsedExport(
            String chatName,
            String chatType,
            String chatId,
            String selfId,
            List<ParsedMessage> messages
    ) {
    }

    private record ParsedMessage(
            String sourceId,
            SenderPayload sender,
            String text,
            Instant timestamp,
            List<AttachmentPayload> attachments,
            List<String> mentions,
            JsonNode rawMessage
    ) {
    }
}
