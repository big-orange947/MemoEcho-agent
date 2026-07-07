package com.memoecho.connector.qqnapcat.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.memoecho.connector.qqnapcat.dto.NapcatApiResponse;
import org.springframework.stereotype.Service;

import java.util.Map;

@Service
public class NapcatMessageService {

    private final NapcatApiClient apiClient;

    public NapcatMessageService(NapcatApiClient apiClient) {
        this.apiClient = apiClient;
    }

    public NapcatApiResponse<JsonNode> sendGroupMessage(Long groupId, Object message) {
        // message 参数既可以是纯文本，也可以是已经组装好的消息段数组。
        return apiClient.call(
                "send_group_msg",
                Map.of("group_id", groupId, "message", message),
                JsonNode.class
        );
    }

    public NapcatApiResponse<JsonNode> sendPrivateMessage(Long userId, Object message) {
        return apiClient.call(
                "send_private_msg",
                Map.of("user_id", userId, "message", message),
                JsonNode.class
        );
    }
}
