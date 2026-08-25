package com.memoecho.connector.qqnapcat.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.memoecho.connector.qqnapcat.config.NapcatApiProperties;
import com.memoecho.connector.qqnapcat.dto.NapcatApiResponse;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpHeaders;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;

import java.util.Map;

@Component
public class NapcatApiClient {

    private static final Logger log = LoggerFactory.getLogger(NapcatApiClient.class);

    private final RestClient restClient;
    private final NapcatApiProperties properties;
    private final ObjectMapper objectMapper;

    public NapcatApiClient(RestClient restClient, NapcatApiProperties properties, ObjectMapper objectMapper) {
        this.restClient = restClient;
        this.properties = properties;
        this.objectMapper = objectMapper;
    }

    public <T> NapcatApiResponse<T> call(String action, Object payload, Class<T> dataType) {
        if (!properties.isEnabled()) {
            log.warn("NapCat API call skipped (disabled): action={}", action);
            return new NapcatApiResponse<>("disabled", -1, null, "NapCat API is disabled.", null, null);
        }

        try {
            // 所有 NapCat 调用都统一走这里，后面如果要补日志、重试或熔断，只改这一层即可。
            JsonNode response = restClient.post()
                    .uri(buildActionUrl(action))
                    .headers(headers -> applyAuth(headers))
                    .body(payload != null ? payload : Map.of())
                    .retrieve()
                    .body(JsonNode.class);

            NapcatApiResponse<T> result = parseResponse(response, dataType);
            log.info("NapCat call finished: action={}, status={}, retcode={}, message={}, wording={}, echo={}",
                    action, result.status(), result.retcode(), result.message(), result.wording(), result.echo());
            return result;
        } catch (RestClientException ex) {
            log.error("NapCat call failed: action={}, error={}", action, ex.getMessage(), ex);
            return new NapcatApiResponse<>("failed", -1, null, ex.getMessage(), null, null);
        }
    }

    private String buildActionUrl(String action) {
        return properties.getBaseUrl().replaceAll("/+$", "") + "/" + action;
    }

    private void applyAuth(HttpHeaders headers) {
        if (properties.getToken() != null && !properties.getToken().isBlank()) {
            headers.setBearerAuth(properties.getToken());
        }
    }

    private <T> NapcatApiResponse<T> parseResponse(JsonNode response, Class<T> dataType) {
        if (response == null || response.isNull()) {
            return new NapcatApiResponse<>("failed", -1, null, "Empty response from NapCat API.", null, null);
        }

        JsonNode dataNode = response.path("data");
        T data = null;
        if (!dataNode.isMissingNode() && !dataNode.isNull()) {
            // data 字段按调用方声明的类型转换，controller 和 service 不需要自己解析 JsonNode。
            data = objectMapper.convertValue(dataNode, dataType);
        }

        return new NapcatApiResponse<>(
                text(response, "status"),
                response.path("retcode").isNumber() ? response.path("retcode").asInt() : null,
                data,
                text(response, "message"),
                text(response, "wording"),
                text(response, "echo")
        );
    }

    private String text(JsonNode node, String fieldName) {
        JsonNode value = node.path(fieldName);
        if (value.isMissingNode() || value.isNull()) {
            return null;
        }
        String result = value.asText();
        return result == null || result.isBlank() ? null : result;
    }
}
