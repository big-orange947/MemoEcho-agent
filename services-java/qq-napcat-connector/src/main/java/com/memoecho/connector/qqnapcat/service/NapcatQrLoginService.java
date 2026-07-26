package com.memoecho.connector.qqnapcat.service;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.google.zxing.BarcodeFormat;
import com.google.zxing.client.j2se.MatrixToImageWriter;
import com.google.zxing.common.BitMatrix;
import com.google.zxing.qrcode.QRCodeWriter;
import com.memoecho.connector.qqnapcat.config.NapcatApiProperties;
import com.memoecho.connector.qqnapcat.config.NapcatWebUiProperties;
import com.memoecho.connector.qqnapcat.dto.NapcatQrLoginResponse;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;

import java.io.ByteArrayOutputStream;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Instant;
import java.util.Base64;
import java.util.Map;

/**
 * 托管 NapCat 的扫码登录过程，并在 QQ 登录成功后自动补齐 Memo Echo 所需的 OneBot 网络配置。
 * WebUI Token 和登录凭证始终留在 Connector 内部，桌面端只能拿到二维码和稳定状态。
 */
@Service
public class NapcatQrLoginService {

    private static final String HTTP_SERVER_NAME = "memo-echo-api";
    private static final String HTTP_CLIENT_NAME = "memo-echo-events";

    private final RestClient restClient;
    private final ObjectMapper objectMapper;
    private final NapcatWebUiProperties webUiProperties;
    private final NapcatApiProperties apiProperties;
    private final NapcatWebUiTokenResolver tokenResolver;

    private volatile String credential = "";
    private volatile Instant credentialExpiresAt = Instant.EPOCH;

    public NapcatQrLoginService(
            RestClient restClient,
            ObjectMapper objectMapper,
            NapcatWebUiProperties webUiProperties,
            NapcatApiProperties apiProperties,
            NapcatWebUiTokenResolver tokenResolver
    ) {
        this.restClient = restClient;
        this.objectMapper = objectMapper;
        this.webUiProperties = webUiProperties;
        this.apiProperties = apiProperties;
        this.tokenResolver = tokenResolver;
    }

    /**
     * 开始扫码登录；如果 QQ 已在线，则直接校验并修复 OneBot 配置。
     */
    public synchronized NapcatQrLoginResponse start() {
        if (!webUiProperties.isEnabled()) {
            return response("DISABLED", "", "扫码登录功能已关闭", "", "", false);
        }
        try {
            ensureCredential();
            return readCurrentState(true);
        } catch (WebUiAuthenticationException exception) {
            return response("SETUP_REQUIRED", "", exception.getMessage(), "", "", false);
        } catch (RestClientException exception) {
            return response("NAPCAT_OFFLINE", "", "未连接到 NapCat，请先启动 NapCat", "", "", false);
        } catch (RuntimeException exception) {
            return response("ERROR", "", safeMessage(exception, "NapCat 扫码登录初始化失败"), "", "", false);
        }
    }

    /**
     * 读取当前扫码状态；桌面端会以较低频率轮询此接口。
     */
    public synchronized NapcatQrLoginResponse status() {
        try {
            ensureCredential();
            return readCurrentState(false);
        } catch (WebUiAuthenticationException exception) {
            return response("SETUP_REQUIRED", "", exception.getMessage(), "", "", false);
        } catch (RestClientException exception) {
            return response("NAPCAT_OFFLINE", "", "NapCat 已离线或正在重启", "", "", false);
        } catch (RuntimeException exception) {
            return response("ERROR", "", safeMessage(exception, "读取扫码状态失败"), "", "", false);
        }
    }

    /**
     * 请求 NapCat 刷新已过期的二维码，并返回新的可显示图片。
     */
    public synchronized NapcatQrLoginResponse refresh() {
        try {
            ensureCredential();
            JsonNode refreshResponse = callWebUi("/QQLogin/RefreshQRcode", Map.of());
            requireSuccess(refreshResponse, "刷新二维码失败");
            return readCurrentState(true);
        } catch (WebUiAuthenticationException exception) {
            return response("SETUP_REQUIRED", "", exception.getMessage(), "", "", false);
        } catch (RestClientException exception) {
            return response("NAPCAT_OFFLINE", "", "NapCat 已离线或正在重启", "", "", false);
        } catch (RuntimeException exception) {
            return response("ERROR", "", safeMessage(exception, "刷新二维码失败"), "", "", false);
        }
    }

    /**
     * 根据 NapCat 的登录状态生成桌面端快照；登录成功后会立即配置 OneBot。
     */
    private NapcatQrLoginResponse readCurrentState(boolean forceQrCode) {
        JsonNode statusResponse = callWebUi("/QQLogin/CheckLoginStatus", Map.of());
        requireSuccess(statusResponse, "读取 QQ 登录状态失败");
        JsonNode statusData = statusResponse.path("data");

        if (statusData.path("isLogin").asBoolean(false)) {
            JsonNode loginInfoResponse = callWebUi("/QQLogin/GetQQLoginInfo", Map.of());
            JsonNode loginInfo = success(loginInfoResponse) ? loginInfoResponse.path("data") : objectMapper.createObjectNode();
            String accountId = firstText(loginInfo, "uin", "user_id", "uid");
            String accountName = firstText(loginInfo, "nick", "nickname", "name");
            boolean configured = ensureOneBotConfig();
            return response(
                    configured ? "CONNECTED" : "CONFIG_FAILED",
                    "",
                    configured ? "QQ 已连接，消息收发配置已完成" : "QQ 已登录，但 OneBot 自动配置失败",
                    accountId,
                    accountName,
                    configured
            );
        }

        if (statusData.path("isOffline").asBoolean(false)) {
            return response("OFFLINE", "", "QQ 已掉线，请重新扫码登录", "", "", false);
        }

        String qrContent = statusData.path("qrcodeurl").asText("");
        if (forceQrCode) {
            JsonNode qrResponse = callWebUi("/QQLogin/GetQQLoginQrcode", Map.of());
            if (success(qrResponse)) {
                qrContent = qrResponse.path("data").path("qrcode").asText(qrContent);
            }
        }
        // 普通状态查询不能主动生成二维码。托管启动器此时可能正在使用 -q 恢复已有会话，
        // 若过早生成二维码，会把一次本可静默完成的快速登录错误地变成重复扫码。
        if (!forceQrCode && qrContent.isBlank()) {
            return response("RESTORING", "", "正在恢复上次 QQ 登录状态", "", "", false);
        }
        if (qrContent.isBlank()) {
            String loginError = statusData.path("loginError").asText("");
            return response("QR_UNAVAILABLE", "", loginError.isBlank() ? "二维码尚未生成，请稍后刷新" : loginError, "", "", false);
        }
        return response("WAITING_SCAN", toQrCodeDataUrl(qrContent), "请使用手机 QQ 扫码并确认登录", "", "", false);
    }

    /**
     * 读取现有 OneBot 配置并只替换 Memo Echo 自己管理的两项，其他配置原样保留。
     */
    private boolean ensureOneBotConfig() {
        JsonNode getResponse = callWebUi("/OB11Config/GetConfig", Map.of());
        requireSuccess(getResponse, "读取 OneBot 配置失败");

        ObjectNode config = normalizeConfig(getResponse.path("data"));
        ObjectNode network = config.with("network");
        ArrayNode httpServers = array(network, "httpServers");
        ArrayNode httpClients = array(network, "httpClients");

        // 同一端口只能由一个 HTTP Server 监听。旧配置若继续启用，请求会命中旧 Token，
        // 表面上扫码成功，实际联系人和消息接口都会返回 token verify failed。
        disableConflictingHttpServers(httpServers);
        removeDuplicateHttpClients(httpClients);
        replaceNamed(httpServers, HTTP_SERVER_NAME, createHttpServer());
        replaceNamed(httpClients, HTTP_CLIENT_NAME, createHttpClient());
        ensureArray(network, "websocketServers");
        ensureArray(network, "websocketClients");

        try {
            JsonNode setResponse = callWebUi("/OB11Config/SetConfig", Map.of("config", objectMapper.writeValueAsString(config)));
            return success(setResponse) && verifyOneBotApi();
        } catch (JsonProcessingException exception) {
            throw new IllegalStateException("序列化 OneBot 配置失败", exception);
        }
    }

    /**
     * 禁用占用 Memo Echo API 端口的旧 HTTP Server，但保留其配置内容供用户后续查看。
     * NapCat 不允许两个 Server 同时监听一个端口，不能仅靠配置名称覆盖来解决冲突。
     */
    private void disableConflictingHttpServers(ArrayNode servers) {
        for (JsonNode item : servers) {
            if (!(item instanceof ObjectNode server)) {
                continue;
            }
            boolean sameManagedServer = HTTP_SERVER_NAME.equals(server.path("name").asText());
            boolean samePort = server.path("port").asInt(-1) == webUiProperties.getOnebotHttpPort();
            if (!sameManagedServer && samePort && server.path("enable").asBoolean(false)) {
                server.put("enable", false);
            }
        }
    }

    /**
     * 删除指向同一事件入口的旧 HTTP Client，防止一条 QQ 消息被重复上报和重复处理。
     * 这里只清理与 Memo Echo 当前回调地址完全相同的条目，不影响用户的其他上报配置。
     */
    private void removeDuplicateHttpClients(ArrayNode clients) {
        String callbackUrl = trimSlash(resolveEventCallbackUrl());
        for (int index = clients.size() - 1; index >= 0; index--) {
            JsonNode client = clients.get(index);
            boolean sameManagedClient = HTTP_CLIENT_NAME.equals(client.path("name").asText());
            boolean sameCallback = callbackUrl.equals(trimSlash(client.path("url").asText("")));
            if (!sameManagedClient && sameCallback) {
                clients.remove(index);
            }
        }
    }

    /**
     * 保存网络配置后实际调用一次 OneBot API，只有请求通过鉴权才向客户端报告连接完成。
     * SetConfig 可能需要短暂重建监听器，因此在有限时间内重试，不做无限等待。
     */
    private boolean verifyOneBotApi() {
        for (int attempt = 0; attempt < 8; attempt++) {
            try {
                JsonNode response = restClient.post()
                        .uri(trimSlash(apiProperties.getBaseUrl()) + "/get_login_info")
                        .headers(this::applyOneBotAuth)
                        .contentType(MediaType.APPLICATION_JSON)
                        .body(Map.of())
                        .retrieve()
                        .body(JsonNode.class);
                if (response != null
                        && "ok".equalsIgnoreCase(response.path("status").asText())
                        && response.path("retcode").asInt(-1) == 0) {
                    return true;
                }
            } catch (RestClientException ignored) {
                // 网络监听器重建期间可能短暂拒绝连接或仍返回旧 Token，下一轮继续验证。
            }
            try {
                Thread.sleep(250L);
            } catch (InterruptedException exception) {
                Thread.currentThread().interrupt();
                return false;
            }
        }
        return false;
    }

    /** 为 OneBot 验证请求添加与业务 API 客户端完全相同的 Bearer Token。 */
    private void applyOneBotAuth(HttpHeaders headers) {
        String token = safe(apiProperties.getToken());
        if (!token.isBlank()) {
            headers.setBearerAuth(token);
        }
    }

    /**
     * 创建供 Java Connector 主动调用 NapCat API 的 HTTP Server 配置。
     */
    private ObjectNode createHttpServer() {
        ObjectNode node = objectMapper.createObjectNode();
        node.put("name", HTTP_SERVER_NAME);
        node.put("enable", true);
        node.put("port", webUiProperties.getOnebotHttpPort());
        // 原生部署只允许本机访问；Docker 部署必须监听所有地址才能接收宿主机请求。
        node.put("host", tokenResolver.isDockerDeployment() ? "0.0.0.0" : "127.0.0.1");
        node.put("enableCors", true);
        node.put("enableWebsocket", false);
        node.put("messagePostFormat", "array");
        node.put("token", safe(apiProperties.getToken()));
        node.put("debug", false);
        return node;
    }

    /**
     * 创建 NapCat 向 Connector 上报消息事件的 HTTP Client 配置。
     * reportSelfMessage 必须开启，后续历史上下文和个人风格样本才包含用户本人发送的消息。
     */
    private ObjectNode createHttpClient() {
        ObjectNode node = objectMapper.createObjectNode();
        node.put("name", HTTP_CLIENT_NAME);
        node.put("enable", true);
        node.put("url", resolveEventCallbackUrl());
        node.put("messagePostFormat", "array");
        node.put("reportSelfMessage", true);
        node.put("token", "");
        node.put("debug", false);
        return node;
    }

    /**
     * 自动选择 NapCat 能访问到的事件回调地址；显式配置始终优先于部署探测结果。
     */
    private String resolveEventCallbackUrl() {
        String configured = safe(webUiProperties.getEventCallbackUrl());
        if (!configured.isBlank()) {
            return configured;
        }
        String host = tokenResolver.isDockerDeployment() ? "host.docker.internal" : "127.0.0.1";
        return "http://" + host + ":8091/api/connectors/qq/napcat/events";
    }

    /**
     * 使用候选 Token 登录 NapCat WebUI，并缓存有效期小于官方一小时上限的凭证。
     */
    private void ensureCredential() {
        if (!credential.isBlank() && Instant.now().isBefore(credentialExpiresAt)) {
            return;
        }
        credential = "";
        credentialExpiresAt = Instant.EPOCH;

        for (String token : tokenResolver.resolveCandidates()) {
            try {
                JsonNode response = restClient.post()
                        .uri(webUiUrl("/auth/login"))
                        // NapCat WebUI 不会把缺少 Content-Type 的 POST 请求按 JSON 接口处理。
                        .contentType(MediaType.APPLICATION_JSON)
                        .body(Map.of("hash", sha256(token + ".napcat")))
                        .retrieve()
                        .body(JsonNode.class);
                if (success(response)) {
                    JsonNode data = response.path("data");
                    if (data.path("require2FA").asBoolean(false)) {
                        throw new WebUiAuthenticationException("NapCat WebUI 已启用二次验证，请先在 WebUI 中完成登录");
                    }
                    String resolvedCredential = data.path("Credential").asText("");
                    if (!resolvedCredential.isBlank()) {
                        credential = resolvedCredential;
                        credentialExpiresAt = Instant.now().plusSeconds(50 * 60L);
                        return;
                    }
                }
            } catch (RestClientException exception) {
                if (isConnectionFailure(exception)) {
                    throw exception;
                }
            }
        }
        throw new WebUiAuthenticationException("无法读取托管 NapCat 的本地登录凭据，请重启 NapCat 后重试");
    }

    /**
     * 调用需要 WebUI 凭证的接口；凭证过期时清空缓存，让下一次请求重新认证。
     */
    private JsonNode callWebUi(String path, Object body) {
        JsonNode response = restClient.post()
                .uri(webUiUrl(path))
                .header(HttpHeaders.AUTHORIZATION, "Bearer " + credential)
                .contentType(MediaType.APPLICATION_JSON)
                .body(body)
                .retrieve()
                .body(JsonNode.class);
        if (response != null && !success(response) && response.path("message").asText("").toLowerCase().contains("authorization")) {
            credential = "";
            credentialExpiresAt = Instant.EPOCH;
        }
        return response;
    }

    private String webUiUrl(String path) {
        return trimSlash(webUiProperties.getBaseUrl()) + normalizePrefix(webUiProperties.getApiPrefix()) + normalizePath(path);
    }

    private String normalizePrefix(String prefix) {
        if (prefix == null || prefix.isBlank() || "/".equals(prefix.trim())) {
            return "";
        }
        return normalizePath(prefix).replaceAll("/+$", "");
    }

    private String normalizePath(String path) {
        return path != null && path.startsWith("/") ? path : "/" + safe(path);
    }

    private String trimSlash(String value) {
        return safe(value).replaceAll("/+$", "");
    }

    private boolean success(JsonNode response) {
        return response != null && !response.isNull() && response.path("code").asInt(-1) == 0;
    }

    private void requireSuccess(JsonNode response, String fallback) {
        if (!success(response)) {
            String message = response == null ? "" : response.path("message").asText("");
            throw new IllegalStateException(message.isBlank() ? fallback : message);
        }
    }

    private ObjectNode normalizeConfig(JsonNode data) {
        if (data != null && data.isObject()) {
            return (ObjectNode) data.deepCopy();
        }
        if (data != null && data.isTextual()) {
            try {
                JsonNode parsed = objectMapper.readTree(data.asText());
                if (parsed.isObject()) {
                    return (ObjectNode) parsed;
                }
            } catch (JsonProcessingException exception) {
                throw new IllegalStateException("NapCat 返回了无法解析的 OneBot 配置", exception);
            }
        }
        return objectMapper.createObjectNode();
    }

    private ArrayNode array(ObjectNode parent, String field) {
        JsonNode value = parent.get(field);
        if (value instanceof ArrayNode arrayNode) {
            return arrayNode;
        }
        return parent.putArray(field);
    }

    private void ensureArray(ObjectNode parent, String field) {
        if (!parent.path(field).isArray()) {
            parent.putArray(field);
        }
    }

    private void replaceNamed(ArrayNode items, String name, ObjectNode replacement) {
        for (int index = items.size() - 1; index >= 0; index--) {
            if (name.equals(items.get(index).path("name").asText())) {
                items.remove(index);
            }
        }
        items.add(replacement);
    }

    private String toQrCodeDataUrl(String content) {
        try {
            BitMatrix matrix = new QRCodeWriter().encode(content, BarcodeFormat.QR_CODE, 320, 320);
            ByteArrayOutputStream output = new ByteArrayOutputStream();
            MatrixToImageWriter.writeToStream(matrix, "PNG", output);
            return "data:image/png;base64," + Base64.getEncoder().encodeToString(output.toByteArray());
        } catch (Exception exception) {
            throw new IllegalStateException("生成登录二维码图片失败", exception);
        }
    }

    private String sha256(String value) {
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8));
            return java.util.HexFormat.of().formatHex(digest);
        } catch (NoSuchAlgorithmException exception) {
            throw new IllegalStateException("当前 Java 环境不支持 SHA-256", exception);
        }
    }

    private String firstText(JsonNode node, String... fields) {
        for (String field : fields) {
            String value = node.path(field).asText("");
            if (!value.isBlank()) {
                return value;
            }
        }
        return "";
    }

    private boolean isConnectionFailure(RestClientException exception) {
        String message = safe(exception.getMessage()).toLowerCase();
        return message.contains("connection refused") || message.contains("connect timed out") || message.contains("i/o error");
    }

    private String safeMessage(RuntimeException exception, String fallback) {
        String message = safe(exception.getMessage());
        return message.isBlank() ? fallback : message;
    }

    private String safe(String value) {
        return value == null ? "" : value.trim();
    }

    private NapcatQrLoginResponse response(
            String state,
            String qrCodeUrl,
            String message,
            String accountId,
            String accountName,
            boolean onebotConfigured
    ) {
        return new NapcatQrLoginResponse(state, qrCodeUrl, message, accountId, accountName, onebotConfigured);
    }

    private static final class WebUiAuthenticationException extends RuntimeException {
        private WebUiAuthenticationException(String message) {
            super(message);
        }
    }
}
