package com.memoecho.connector.qqnapcat.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.memoecho.connector.qqnapcat.dto.NapcatApiResponse;
import org.springframework.stereotype.Service;

import java.util.Map;

/**
 * 封装 NapCat 好友列表能力，供上层在创建会话规则时选择私聊对象。
 */
@Service
public class NapcatContactService {

    private final NapcatApiClient apiClient;

    /**
     * 注入通用 NapCat API 客户端，避免联系人能力重复处理鉴权和错误转换。
     */
    public NapcatContactService(NapcatApiClient apiClient) {
        this.apiClient = apiClient;
    }

    /**
     * 读取当前 QQ 账号的好友列表，返回 NapCat 原始结构供事件中心归一化。
     */
    public NapcatApiResponse<JsonNode> getFriendList() {
        return apiClient.call("get_friend_list", Map.of(), JsonNode.class);
    }
}
