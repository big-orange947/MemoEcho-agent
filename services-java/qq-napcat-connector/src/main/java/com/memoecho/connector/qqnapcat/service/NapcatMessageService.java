package com.memoecho.connector.qqnapcat.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.memoecho.connector.qqnapcat.dto.NapcatApiResponse;
import org.springframework.stereotype.Service;

import java.util.List;
import java.util.Map;

@Service
public class NapcatMessageService {

    private final NapcatApiClient apiClient;
    private final OutboundMessageRegistry outboundMessageRegistry;
    private final OutboundRequestDeduplicator requestDeduplicator;

    public NapcatMessageService(
            NapcatApiClient apiClient,
            OutboundMessageRegistry outboundMessageRegistry,
            OutboundRequestDeduplicator requestDeduplicator
    ) {
        this.apiClient = apiClient;
        this.outboundMessageRegistry = outboundMessageRegistry;
        this.requestDeduplicator = requestDeduplicator;
    }

    /** 发送群消息，并登记 Runtime 提供的消息关联身份。 */
    public NapcatApiResponse<JsonNode> sendGroupMessage(
            Long groupId,
            Object message,
            String clientMessageId,
            String correlationId
    ) {
        String normalizedText = normalizeMessage(message);
        outboundMessageRegistry.registerPending(
                "group", String.valueOf(groupId), normalizedText, clientMessageId, correlationId);
        NapcatApiResponse<JsonNode> response = requestDeduplicator.execute(
                clientMessageId,
                () -> apiClient.call(
                        "send_group_msg",
                        Map.of("group_id", groupId, "message", message),
                        JsonNode.class
                )
        );
        registerCompletedMessage(
                "group", String.valueOf(groupId), normalizedText, clientMessageId, correlationId, response);
        return response;
    }

    /** 发送私聊消息，并登记 Runtime 提供的消息关联身份。 */
    public NapcatApiResponse<JsonNode> sendPrivateMessage(
            Long userId,
            Object message,
            String clientMessageId,
            String correlationId
    ) {
        String normalizedText = normalizeMessage(message);
        outboundMessageRegistry.registerPending(
                "private", String.valueOf(userId), normalizedText, clientMessageId, correlationId);
        NapcatApiResponse<JsonNode> response = requestDeduplicator.execute(
                clientMessageId,
                () -> apiClient.call(
                        "send_private_msg",
                        Map.of("user_id", userId, "message", message),
                        JsonNode.class
                )
        );
        registerCompletedMessage(
                "private", String.valueOf(userId), normalizedText, clientMessageId, correlationId, response);
        return response;
    }

    /** 从 NapCat 成功响应中提取 message_id，建立平台级精确关联。 */
    private void registerCompletedMessage(
            String chatType,
            String chatId,
            String normalizedText,
            String clientMessageId,
            String correlationId,
            NapcatApiResponse<JsonNode> response
    ) {
        if (response == null || response.data() == null || !"ok".equalsIgnoreCase(response.status())) {
            return;
        }
        String messageId = response.data().path("message_id").asText("");
        outboundMessageRegistry.complete(
                chatType, chatId, normalizedText, messageId, clientMessageId, correlationId);
    }

    /** 将纯文本或 OneBot 消息段转成稳定文本，仅用于发送期间的短暂竞态匹配。 */
    private String normalizeMessage(Object message) {
        if (message instanceof String text) {
            return normalizeText(text);
        }
        if (!(message instanceof List<?> segments)) {
            return normalizeText(String.valueOf(message));
        }
        StringBuilder text = new StringBuilder();
        for (Object segment : segments) {
            if (!(segment instanceof Map<?, ?> segmentMap)) {
                continue;
            }
            Object type = segmentMap.get("type");
            Object data = segmentMap.get("data");
            if ("text".equals(type) && data instanceof Map<?, ?> dataMap) {
                Object value = dataMap.get("text");
                text.append(value == null ? "" : value);
            }
        }
        return normalizeText(text.toString());
    }

    private String normalizeText(String text) {
        return text == null ? "" : text.replaceAll("\\s+", "").trim();
    }
}
