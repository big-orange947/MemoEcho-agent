package com.memoecho.eventcenter.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.memoecho.eventcenter.config.DownstreamServiceProperties;
import com.memoecho.eventcenter.dto.QqMessageSendResult;
import com.memoecho.eventcenter.dto.UnifiedEventPayload;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

@Component
public class QqConnectorMessageClient {

    private final RestClient restClient;
    private final DownstreamServiceProperties properties;

    public QqConnectorMessageClient(RestClient restClient, DownstreamServiceProperties properties) {
        // 这个构造函数的作用是注入 Connector HTTP 客户端和地址配置，集中管理确认草稿后的 QQ 发送动作。
        this.restClient = restClient;
        this.properties = properties;
    }

    public QqMessageSendResult sendText(UnifiedEventPayload event, String message) {
        // 这个函数的作用是根据原始事件的会话类型选择私聊或群聊发送接口，确保草稿只回写到来源会话。
        if (!"qq".equalsIgnoreCase(event.platform())) {
            return new QqMessageSendResult(false, "当前仅支持向 QQ 会话确认发送草稿。");
        }
        if (event.chatId() == null || event.chatId().isBlank()) {
            return new QqMessageSendResult(false, "原始事件缺少 chatId，无法确认发送草稿。");
        }
        try {
            long chatId = Long.parseLong(event.chatId());
            String path = "group".equalsIgnoreCase(event.chatType())
                    ? "/internal/napcat/messages/group"
                    : "/internal/napcat/messages/private";
            String identifier = "group".equalsIgnoreCase(event.chatType()) ? "groupId" : "userId";
            Map<String, Object> request = new LinkedHashMap<>();
            request.put(identifier, chatId);
            applyMessageBody(request, event, message);

            JsonNode response = restClient.post()
                    .uri(properties.getQqConnectorBaseUrl() + path)
                    .body(request)
                    .retrieve()
                    .body(JsonNode.class);
            boolean successful = response != null
                    && "ok".equalsIgnoreCase(response.path("status").asText())
                    && response.path("retcode").asInt(-1) == 0;
            String summary = response == null ? "QQ Connector 没有返回响应。" : response.path("message").asText("");
            return new QqMessageSendResult(successful, summary);
        } catch (NumberFormatException ex) {
            return new QqMessageSendResult(false, "chatId 不是合法的 QQ 数字标识。");
        } catch (RestClientException ex) {
            return new QqMessageSendResult(false, "调用 QQ Connector 失败：" + ex.getMessage());
        }
    }

    private void applyMessageBody(Map<String, Object> request, UnifiedEventPayload event, String message) {
        // 这个函数的作用是还原群聊 @ 触发时的回复上下文；普通私聊或群聊仍然使用纯文本发送。
        boolean shouldMentionSender = "group".equalsIgnoreCase(event.chatType())
                && event.selfId() != null
                && event.mentions() != null
                && event.mentions().contains(event.selfId())
                && event.sender() != null
                && event.sender().id() != null
                && !event.sender().id().isBlank();
        if (!shouldMentionSender) {
            request.put("message", message);
            return;
        }
        request.put("segments", List.of(
                Map.of("type", "at", "data", Map.of("qq", event.sender().id())),
                Map.of("type", "text", "data", Map.of("text", " " + message))
        ));
    }
}
