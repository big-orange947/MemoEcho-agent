package com.memoecho.connector.qqnapcat.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.memoecho.connector.qqnapcat.dto.NapcatApiResponse;
import org.springframework.stereotype.Service;

import java.util.Map;

@Service
public class NapcatGroupService {

    private final NapcatApiClient apiClient;

    public NapcatGroupService(NapcatApiClient apiClient) {
        this.apiClient = apiClient;
    }

    public NapcatApiResponse<JsonNode> getGroupList() {
        // 群列表通常作为“选择可管理群聊”的基础数据来源。
        return apiClient.call("get_group_list", Map.of(), JsonNode.class);
    }

    public NapcatApiResponse<JsonNode> getGroupMemberList(Long groupId) {
        return apiClient.call(
                "get_group_member_list",
                Map.of("group_id", groupId),
                JsonNode.class
        );
    }

    public NapcatApiResponse<JsonNode> getGroupMessageHistory(Long groupId, Integer count) {
        // 历史消息读取暂时透传原始结果，后面再按 Agent 需要做结构化。
        return apiClient.call(
                "get_group_msg_history",
                Map.of("group_id", groupId, "count", count),
                JsonNode.class
        );
    }
}
