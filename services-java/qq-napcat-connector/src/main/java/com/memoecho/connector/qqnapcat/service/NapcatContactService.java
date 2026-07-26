package com.memoecho.connector.qqnapcat.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.memoecho.connector.qqnapcat.dto.NapcatApiResponse;
import com.memoecho.connector.qqnapcat.dto.NapcatLoginInfoData;
import com.memoecho.connector.qqnapcat.dto.NapcatOwnHistoryData;
import com.memoecho.connector.qqnapcat.dto.NapcatPrivateHistoryData;
import org.springframework.stereotype.Service;

import java.util.Map;
import java.util.List;
import java.util.ArrayList;

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

    /**
     * 获取指定私聊的历史，并只保留发送者为当前登录 QQ 的消息。
     * 用户开启训练授权后，上层会把这些记录统一视为本人历史表达样本。
     */
    public NapcatApiResponse<NapcatOwnHistoryData> getOwnFriendMessageHistory(String userId, Integer count) {
        int safeCount = count == null ? 100 : Math.min(Math.max(count, 1), 500);
        NapcatApiResponse<NapcatLoginInfoData> login = apiClient.call("get_login_info", Map.of(), NapcatLoginInfoData.class);
        if (login.data() == null || login.data().userId() == null) {
            return new NapcatApiResponse<>("failed", -1, null, "无法读取当前登录 QQ。", null, null);
        }

        String selfId = String.valueOf(login.data().userId());
        NapcatApiResponse<JsonNode> history = apiClient.call(
                "get_friend_msg_history",
                Map.of("user_id", userId, "message_seq", "0", "count", safeCount, "reverseOrder", false),
                JsonNode.class
        );
        if (history.data() == null) {
            return new NapcatApiResponse<>(history.status(), history.retcode(), null,
                    history.message(), history.wording(), history.echo());
        }

        JsonNode messagesNode = history.data().path("messages");
        List<JsonNode> ownMessages = new ArrayList<>();
        if (messagesNode.isArray()) {
            for (JsonNode message : messagesNode) {
                String senderId = message.path("sender").path("user_id").asText(
                        message.path("user_id").asText(""));
                if (selfId.equals(senderId)) {
                    ownMessages.add(message.deepCopy());
                }
            }
        }
        return new NapcatApiResponse<>(history.status(), history.retcode(),
                new NapcatOwnHistoryData(selfId, List.copyOf(ownMessages)),
                history.message(), history.wording(), history.echo());
    }

    /**
     * 获取指定私聊的完整最近历史，供用户明确授权的上下文同步使用。
     * 调用方负责按本地用户权限决定是否持久化，Connector 只负责转发 NapCat 数据。
     */
    public NapcatApiResponse<NapcatPrivateHistoryData> getFriendMessageHistory(String userId, Integer count) {
        int safeCount = count == null ? 100 : Math.min(Math.max(count, 1), 500);
        NapcatApiResponse<NapcatLoginInfoData> login = apiClient.call("get_login_info", Map.of(), NapcatLoginInfoData.class);
        if (login.data() == null || login.data().userId() == null) {
            return new NapcatApiResponse<>("failed", -1, null, "无法读取当前登录 QQ", null, null);
        }

        NapcatApiResponse<JsonNode> history = apiClient.call(
                "get_friend_msg_history",
                Map.of("user_id", userId, "message_seq", "0", "count", safeCount, "reverseOrder", false),
                JsonNode.class
        );
        if (history.data() == null) {
            return new NapcatApiResponse<>(history.status(), history.retcode(), null,
                    history.message(), history.wording(), history.echo());
        }

        List<JsonNode> messages = new ArrayList<>();
        JsonNode messageNode = history.data().path("messages");
        if (messageNode.isArray()) {
            messageNode.forEach(message -> messages.add(message.deepCopy()));
        }
        return new NapcatApiResponse<>(history.status(), history.retcode(),
                new NapcatPrivateHistoryData(String.valueOf(login.data().userId()), List.copyOf(messages)),
                history.message(), history.wording(), history.echo());
    }
}
