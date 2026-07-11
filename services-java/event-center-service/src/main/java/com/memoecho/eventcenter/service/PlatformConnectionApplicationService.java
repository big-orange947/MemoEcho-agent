package com.memoecho.eventcenter.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.memoecho.eventcenter.config.DownstreamServiceProperties;
import com.memoecho.eventcenter.dto.PlatformConnectionResponse;
import com.memoecho.eventcenter.dto.PlatformConnectionUpsertRequest;
import com.memoecho.eventcenter.model.PlatformConnection;
import com.memoecho.eventcenter.repository.PlatformConnectionRepository;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestClient;
import org.springframework.web.server.ResponseStatusException;

import java.time.Instant;
import java.util.List;
import java.util.Locale;
import java.util.UUID;

@Service
public class PlatformConnectionApplicationService {

    private final PlatformConnectionRepository repository;
    private final ApiKeyCryptoService cryptoService;
    private final DownstreamServiceProperties properties;

    public PlatformConnectionApplicationService(
            PlatformConnectionRepository repository,
            ApiKeyCryptoService cryptoService,
            DownstreamServiceProperties properties
    ) {
        // 这个构造函数的作用是注入连接仓储、凭据加密器和默认 Connector 地址。
        this.repository = repository;
        this.cryptoService = cryptoService;
        this.properties = properties;
    }

    public List<PlatformConnectionResponse> listConnections(String userId) {
        // 这个函数的作用是列出当前用户的连接；本地首次使用时自动创建 QQ/NapCat 默认档案。
        ensureLocalQqConnection(userId);
        return repository.findAllByUserId(userId).stream().map(this::toResponse).toList();
    }

    public PlatformConnectionResponse create(String userId, PlatformConnectionUpsertRequest request) {
        // 这个函数的作用是创建用户连接并加密只写凭据，不主动把凭据发送给下游服务。
        Instant now = Instant.now();
        PlatformConnection connection = new PlatformConnection(
                UUID.randomUUID().toString(), userId, request.name().trim(), normalize(request.platform()),
                normalize(request.connector()), request.enabled() == null || request.enabled(),
                resolveBaseUrl(request), cryptoService.encrypt(request.credential()), "", "",
                "UNKNOWN", "尚未执行健康检查。", null, now, now);
        return toResponse(repository.save(connection));
    }

    public PlatformConnectionResponse update(String userId, String connectionId, PlatformConnectionUpsertRequest request) {
        // 这个函数的作用是更新当前用户拥有的连接；未传凭据时保留原密文，避免编辑名称导致凭据丢失。
        PlatformConnection existing = requireOwned(userId, connectionId);
        String credential = request.credential() == null || request.credential().isBlank()
                ? existing.credentialCiphertext() : cryptoService.encrypt(request.credential());
        PlatformConnection updated = new PlatformConnection(
                existing.id(), existing.userId(), request.name().trim(), normalize(request.platform()),
                normalize(request.connector()), request.enabled() == null || request.enabled(),
                resolveBaseUrl(request), credential, existing.accountId(), existing.accountName(),
                "UNKNOWN", "连接配置已更新，等待健康检查。", null, existing.createdAt(), Instant.now());
        return toResponse(repository.save(updated));
    }

    public void delete(String userId, String connectionId) {
        // 这个函数的作用是验证所有权后删除连接档案及其加密凭据。
        requireOwned(userId, connectionId);
        repository.deleteByIdAndUserId(connectionId, userId);
    }

    public PlatformConnectionResponse checkHealth(String userId, String connectionId) {
        // 这个函数的作用是检查指定连接并持久化脱敏后的账号与健康状态。
        PlatformConnection connection = requireOwned(userId, connectionId);
        PlatformConnection checked = check(connection);
        return toResponse(repository.save(checked));
    }

    private PlatformConnection check(PlatformConnection connection) {
        if (!connection.enabled()) {
            return withHealth(connection, "DISABLED", "连接已停用。", "", "");
        }
        if (!"qq".equals(connection.platform()) || !"napcat".equals(connection.connector())) {
            return withHealth(connection, "UNSUPPORTED", "当前版本尚未实现该 Connector 的健康检查。", "", "");
        }
        try {
            RestClient client = RestClient.builder().baseUrl(connection.connectorBaseUrl()).build();
            JsonNode login = client.get().uri("/internal/napcat/login-info").retrieve().body(JsonNode.class);
            JsonNode status = client.get().uri("/internal/napcat/status").retrieve().body(JsonNode.class);
            boolean online = login != null && status != null
                    && "ok".equals(login.path("status").asText())
                    && "ok".equals(status.path("status").asText())
                    && status.path("data").path("online").asBoolean(false);
            return withHealth(connection, online ? "HEALTHY" : "UNAVAILABLE",
                    online ? "NapCat 已连接。" : "NapCat 未登录或状态异常。",
                    login == null ? "" : login.path("data").path("user_id").asText(""),
                    login == null ? "" : login.path("data").path("nickname").asText(""));
        } catch (Exception exception) {
            return withHealth(connection, "OFFLINE", "无法连接 QQ Connector。", "", "");
        }
    }

    private PlatformConnection withHealth(
            PlatformConnection connection, String health, String message,
            String accountId, String accountName
    ) {
        // 这个函数的作用是创建健康检查后的不可变连接快照。
        Instant now = Instant.now();
        return new PlatformConnection(
                connection.id(), connection.userId(), connection.name(), connection.platform(), connection.connector(),
                connection.enabled(), connection.connectorBaseUrl(), connection.credentialCiphertext(), accountId,
                accountName, health, message, now, connection.createdAt(), now);
    }

    private PlatformConnection requireOwned(String userId, String connectionId) {
        // 这个函数的作用是统一执行连接所有权校验，不存在或属于其他用户时均返回 404。
        return repository.findByIdAndUserId(connectionId, userId)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "找不到当前用户的平台连接。"));
    }

    private void ensureLocalQqConnection(String userId) {
        // 这个函数的作用是为首次使用的本地用户创建可立即健康检查的 QQ/NapCat 默认档案。
        if (!repository.findAllByUserId(userId).isEmpty()) {
            return;
        }
        Instant now = Instant.now();
        repository.save(new PlatformConnection(
                UUID.randomUUID().toString(), userId, "本地 QQ / NapCat", "qq", "napcat", true,
                properties.getQqConnectorBaseUrl(), "", "", "", "UNKNOWN", "等待健康检查。",
                null, now, now));
    }

    private String resolveBaseUrl(PlatformConnectionUpsertRequest request) {
        // 这个函数的作用是规范用户传入的 Connector 地址，QQ 未传地址时使用系统默认值。
        if (request.connectorBaseUrl() != null && !request.connectorBaseUrl().isBlank()) {
            return request.connectorBaseUrl().trim().replaceAll("/+$", "");
        }
        return "qq".equals(normalize(request.platform())) ? properties.getQqConnectorBaseUrl() : "";
    }

    private String normalize(String value) {
        // 这个函数的作用是把平台和 Connector 标识统一成去空格的小写形式。
        return value == null ? "" : value.trim().toLowerCase(Locale.ROOT);
    }

    private PlatformConnectionResponse toResponse(PlatformConnection connection) {
        // 这个函数的作用是生成脱敏响应，明确排除 credentialCiphertext。
        return new PlatformConnectionResponse(
                connection.id(), connection.userId(), connection.name(), connection.platform(), connection.connector(),
                connection.enabled(), "HEALTHY".equals(connection.health()), connection.accountId(),
                connection.accountName(), connection.health(), connection.healthMessage(),
                connection.lastCheckedAt() == null ? null : connection.lastCheckedAt().toString(),
                connection.credentialCiphertext() != null && !connection.credentialCiphertext().isBlank());
    }
}
