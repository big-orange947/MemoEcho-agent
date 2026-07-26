package com.memoecho.eventcenter.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

import java.util.List;

@ConfigurationProperties(prefix = "event-center.security")
public class EventCenterSecurityProperties {

    private String apiKeySecret = "memo-echo-dev-secret-change-me";
    private String jwtSecret = "memo-echo-jwt-dev-secret-change-me";
    private long jwtExpiresSeconds = 604800;
    private boolean allowLegacyUserHeader = false;
    private String runtimeToken = "memo-echo-local-runtime-token";
    private List<String> allowedOrigins = List.of(
            "http://127.0.0.1:5173",
            "http://tauri.localhost",
            "tauri://localhost"
    );

    /**
     * 这个函数的作用是返回用于 API Key 加解密的主密钥。
     */
    public String getApiKeySecret() {
        return apiKeySecret;
    }

    /**
     * 这个函数的作用是允许通过配置文件或环境变量覆盖默认主密钥。
     */
    public void setApiKeySecret(String apiKeySecret) {
        this.apiKeySecret = apiKeySecret;
    }

    public String getJwtSecret() {
        // 这个函数的作用是返回 JWT HMAC 签名密钥。
        return jwtSecret;
    }

    public void setJwtSecret(String jwtSecret) {
        // 这个函数的作用是允许部署环境覆盖默认 JWT 密钥。
        this.jwtSecret = jwtSecret;
    }

    public long getJwtExpiresSeconds() {
        // 这个函数的作用是返回登录令牌有效期秒数。
        return jwtExpiresSeconds;
    }

    public void setJwtExpiresSeconds(long jwtExpiresSeconds) {
        // 这个函数的作用是配置登录令牌有效期，并保证至少为一分钟。
        this.jwtExpiresSeconds = Math.max(jwtExpiresSeconds, 60);
    }

    public boolean isAllowLegacyUserHeader() {
        // 这个函数的作用是控制开发期是否允许旧用户头作为身份回退。
        return allowLegacyUserHeader;
    }

    public void setAllowLegacyUserHeader(boolean allowLegacyUserHeader) {
        // 这个函数的作用是允许部署环境关闭可伪造的开发期用户头。
        this.allowLegacyUserHeader = allowLegacyUserHeader;
    }

    /**
     * 返回 Python runtime 调用内部受限接口时使用的服务令牌。
     */
    public String getRuntimeToken() {
        return runtimeToken;
    }

    /**
     * 设置 Python runtime 的服务令牌；生产环境必须通过环境变量注入高熵随机值。
     */
    public void setRuntimeToken(String runtimeToken) {
        this.runtimeToken = runtimeToken == null ? "" : runtimeToken.trim();
    }

    /**
     * 返回允许调用本地 event-center 的桌面客户端来源白名单。
     */
    public List<String> getAllowedOrigins() {
        return allowedOrigins;
    }

    /**
     * 允许部署环境覆盖桌面客户端来源白名单，避免开放任意跨域访问。
     */
    public void setAllowedOrigins(List<String> allowedOrigins) {
        this.allowedOrigins = allowedOrigins == null ? List.of() : allowedOrigins.stream()
                .filter(origin -> origin != null && !origin.isBlank())
                .map(String::trim)
                .distinct()
                .toList();
    }
}
