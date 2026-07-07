package com.memoecho.eventcenter.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.memoecho.eventcenter.config.AgentRuntimeDispatchProperties;
import com.memoecho.eventcenter.dto.DispatchResult;
import com.memoecho.eventcenter.dto.UnifiedEventPayload;
import org.springframework.http.ResponseEntity;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;

@Component
public class AgentRuntimeDispatchClient {

    private final RestClient restClient;
    private final AgentRuntimeDispatchProperties properties;

    public AgentRuntimeDispatchClient(RestClient restClient, AgentRuntimeDispatchProperties properties) {
        this.restClient = restClient;
        this.properties = properties;
    }

    public DispatchResult dispatch(UnifiedEventPayload payload) {
        if (!properties.isEnabled()) {
            return new DispatchResult(false, null, null, null);
        }

        try {
            ResponseEntity<JsonNode> response = restClient.post()
                    .uri(properties.getBaseUrl() + properties.getHandlePath())
                    .body(payload)
                    .retrieve()
                    .toEntity(JsonNode.class);

            return new DispatchResult(
                    true,
                    response.getStatusCode().value(),
                    response.getBody(),
                    null
            );
        } catch (RestClientException ex) {
            return new DispatchResult(true, null, null, ex.getMessage());
        }
    }
}
