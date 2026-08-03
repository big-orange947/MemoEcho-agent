package com.memoecho.eventcenter.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.memoecho.eventcenter.config.DownstreamServiceProperties;
import com.memoecho.eventcenter.dto.QqContactResponse;
import com.memoecho.eventcenter.model.PlatformConnection;
import com.memoecho.eventcenter.repository.PlatformConnectionRepository;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;
import org.springframework.web.server.ResponseStatusException;

import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;

/**
 * 从 QQ/NapCat Connector 读取好友和群聊，并归一化成设定集可选择的会话候选项。
 */
@Component
public class QqConnectorContactClient {

    private final RestClient restClient;
    private final DownstreamServiceProperties properties;
    private final PlatformConnectionRepository connectionRepository;

    /**
     * 注入 Connector 地址配置，避免桌面客户端直接接触 NapCat 端口和令牌。
     */
    public QqConnectorContactClient(
            RestClient restClient,
            DownstreamServiceProperties properties,
            PlatformConnectionRepository connectionRepository
    ) {
        this.restClient = restClient;
        this.properties = properties;
        this.connectionRepository = connectionRepository;
    }

    /**
     * 拉取当前用户 QQ 连接下的好友和群聊候选项。
     * Connector 离线时必须明确报错，不能把故障伪装成“账号没有好友”。
     */
    public List<QqContactResponse> listContacts(String userId) {
        String connectorBaseUrl = resolveConnectorBaseUrl(userId);
        List<QqContactResponse> contacts = new ArrayList<>();
        contacts.addAll(readFriends(connectorBaseUrl));
        contacts.addAll(readGroups(connectorBaseUrl));
        return contacts;
    }

    /**
     * 解析 Connector 返回的好友列表，优先展示备注名，其次展示昵称。
     */
    private List<QqContactResponse> readFriends(String connectorBaseUrl) {
        JsonNode data = getData(connectorBaseUrl, "/internal/napcat/friends", "好友");
        List<QqContactResponse> result = new ArrayList<>();
        for (JsonNode item : data) {
            String id = item.path("user_id").asText("");
            String nickname = item.path("nickname").asText("");
            String remark = item.path("remark").asText("");
            if (!id.isBlank()) {
                result.add(new QqContactResponse(
                        id,
                        remark.isBlank() ? nickname : remark,
                        "private",
                        remark,
                        collectAliases(remark, nickname, id)
                ));
            }
        }
        return result;
    }

    /**
     * 解析 Connector 返回的群聊列表，群备注存在时优先作为展示名称。
     */
    private List<QqContactResponse> readGroups(String connectorBaseUrl) {
        JsonNode data = getData(connectorBaseUrl, "/internal/napcat/groups", "群聊");
        List<QqContactResponse> result = new ArrayList<>();
        for (JsonNode item : data) {
            String id = item.path("group_id").asText("");
            String groupName = item.path("group_name").asText("");
            String remark = item.path("group_remark").asText("");
            if (!id.isBlank()) {
                result.add(new QqContactResponse(
                        id,
                        remark.isBlank() ? groupName : remark,
                        "group",
                        remark,
                        collectAliases(remark, groupName, id)
                ));
            }
        }
        return result;
    }

    /**
     * 汇总 NapCat 提供的备注、昵称和 QQ 号。
     * RouterAgent 可据此匹配用户习惯使用的任意称呼，而展示名称仍只保留一个。
     */
    private List<String> collectAliases(String... values) {
        LinkedHashSet<String> aliases = new LinkedHashSet<>();
        for (String value : values) {
            if (value != null && !value.isBlank()) {
                aliases.add(value.trim());
            }
        }
        return List.copyOf(aliases);
    }

    /**
     * 优先使用当前用户保存的平台连接地址，避免多用户或自定义端口时误连全局默认 Connector。
     */
    private String resolveConnectorBaseUrl(String userId) {
        return connectionRepository.findAllByUserId(userId).stream()
                .filter(PlatformConnection::enabled)
                .filter(connection -> "qq".equalsIgnoreCase(connection.platform()))
                .filter(connection -> "napcat".equalsIgnoreCase(connection.connector()))
                .map(PlatformConnection::connectorBaseUrl)
                .filter(baseUrl -> baseUrl != null && !baseUrl.isBlank())
                .findFirst()
                .orElse(properties.getQqConnectorBaseUrl());
    }

    /**
     * 调用 Connector 并校验 NapCat 标准响应；失败状态会转换成桌面端可读的网关错误。
     */
    private JsonNode getData(String connectorBaseUrl, String path, String contactType) {
        try {
            JsonNode response = restClient.get()
                    .uri(trimTrailingSlash(connectorBaseUrl) + path)
                    .retrieve()
                    .body(JsonNode.class);
            if (response == null || !"ok".equalsIgnoreCase(response.path("status").asText())) {
                String detail = response == null ? "Connector 未返回响应" : response.path("message").asText("");
                throw unavailable(contactType, detail);
            }
            JsonNode data = response.path("data");
            if (!data.isArray()) {
                throw unavailable(contactType, "NapCat 返回的数据格式不正确");
            }
            return data;
        } catch (ResponseStatusException exception) {
            throw exception;
        } catch (RestClientException exception) {
            throw unavailable(contactType, exception.getMessage());
        }
    }

    /** 统一生成不泄露内部凭据、但足够指导用户恢复运行时的错误信息。 */
    private ResponseStatusException unavailable(String contactType, String detail) {
        String suffix = detail == null || detail.isBlank() ? "" : "（" + detail + "）";
        return new ResponseStatusException(
                HttpStatus.BAD_GATEWAY,
                "暂时无法读取 QQ " + contactType + "列表，请确认 NapCat 已启动并登录后重试。" + suffix
        );
    }

    /** 去掉连接地址末尾的斜杠，避免拼接内部路径时出现双斜杠。 */
    private String trimTrailingSlash(String value) {
        String normalized = value == null ? "" : value.trim();
        while (normalized.endsWith("/")) {
            normalized = normalized.substring(0, normalized.length() - 1);
        }
        return normalized;
    }
}
