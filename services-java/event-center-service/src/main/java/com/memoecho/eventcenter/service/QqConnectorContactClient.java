package com.memoecho.eventcenter.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.memoecho.eventcenter.config.DownstreamServiceProperties;
import com.memoecho.eventcenter.dto.QqContactResponse;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;

import java.util.ArrayList;
import java.util.List;

/**
 * 从 QQ/NapCat Connector 读取好友和群聊，并归一化成设定集可选择的会话候选项。
 */
@Component
public class QqConnectorContactClient {

    private final RestClient restClient;
    private final DownstreamServiceProperties properties;

    /**
     * 注入 Connector 地址配置，避免桌面客户端直接接触 NapCat 端口和令牌。
     */
    public QqConnectorContactClient(RestClient restClient, DownstreamServiceProperties properties) {
        this.restClient = restClient;
        this.properties = properties;
    }

    /**
     * 拉取好友和群聊候选项；Connector 不可用时返回空列表，让客户端仍可手动配置范围。
     */
    public List<QqContactResponse> listContacts() {
        List<QqContactResponse> contacts = new ArrayList<>();
        contacts.addAll(readFriends());
        contacts.addAll(readGroups());
        return contacts;
    }

    /**
     * 解析 Connector 返回的好友列表，优先展示备注名，其次展示昵称。
     */
    private List<QqContactResponse> readFriends() {
        try {
            JsonNode data = getData("/internal/napcat/friends");
            if (data == null || !data.isArray()) {
                return List.of();
            }
            List<QqContactResponse> result = new ArrayList<>();
            for (JsonNode item : data) {
                String id = item.path("user_id").asText("");
                String nickname = item.path("nickname").asText("");
                String remark = item.path("remark").asText("");
                if (!id.isBlank()) {
                    result.add(new QqContactResponse(id, remark.isBlank() ? nickname : remark, "private", remark));
                }
            }
            return result;
        } catch (RestClientException exception) {
            return List.of();
        }
    }

    /**
     * 解析 Connector 返回的群聊列表，群备注存在时优先作为展示名称。
     */
    private List<QqContactResponse> readGroups() {
        try {
            JsonNode data = getData("/internal/napcat/groups");
            if (data == null || !data.isArray()) {
                return List.of();
            }
            List<QqContactResponse> result = new ArrayList<>();
            for (JsonNode item : data) {
                String id = item.path("group_id").asText("");
                String groupName = item.path("group_name").asText("");
                String remark = item.path("group_remark").asText("");
                if (!id.isBlank()) {
                    result.add(new QqContactResponse(id, remark.isBlank() ? groupName : remark, "group", remark));
                }
            }
            return result;
        } catch (RestClientException exception) {
            return List.of();
        }
    }

    /**
     * 调用 Connector 内部接口并仅抽取 NapCat 标准响应中的 data 数组。
     */
    private JsonNode getData(String path) {
        JsonNode response = restClient.get()
                .uri(properties.getQqConnectorBaseUrl() + path)
                .retrieve()
                .body(JsonNode.class);
        return response == null ? null : response.path("data");
    }
}
