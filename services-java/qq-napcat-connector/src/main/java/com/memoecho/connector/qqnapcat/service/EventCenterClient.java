package com.memoecho.connector.qqnapcat.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.memoecho.connector.qqnapcat.config.EventCenterProperties;
import com.memoecho.connector.qqnapcat.dto.EventCenterResponse;
import com.memoecho.connector.qqnapcat.dto.UnifiedEventPayload;
import org.springframework.http.ResponseEntity;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;

@Component
public class EventCenterClient {

    private final RestClient restClient;
    private final EventCenterProperties properties;

    public EventCenterClient(RestClient restClient, EventCenterProperties properties) {
        this.restClient = restClient;
        this.properties = properties;
    }

    public EventCenterResponse forward(UnifiedEventPayload payload) {
        if (!properties.isForwardEnabled()) {
            return new EventCenterResponse(false, null, null, null);
        }

        try {
            // connector 只负责把统一事件往 event-center 推，不在这里掺杂路由或业务判断。
            ResponseEntity<JsonNode> response = restClient.post()
                    .uri(properties.getBaseUrl() + properties.getIngestPath())
                    .body(payload)
                    .retrieve()
                    .toEntity(JsonNode.class);

            return new EventCenterResponse(
                    true,
                    response.getStatusCode().value(),
                    response.getBody(),
                    null
            );
        } catch (RestClientException ex) {
            // 这里保留错误信息给上层日志使用，方便快速区分“NapCat 收到了”和“event-center 没收进去”。
            return new EventCenterResponse(true, null, null, ex.getMessage());
        }
    }
}
