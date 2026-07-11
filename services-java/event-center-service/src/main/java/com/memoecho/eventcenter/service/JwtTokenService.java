package com.memoecho.eventcenter.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.memoecho.eventcenter.config.EventCenterSecurityProperties;
import com.memoecho.eventcenter.dto.AuthenticatedUser;
import com.memoecho.eventcenter.model.LocalUser;
import org.springframework.stereotype.Service;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.Base64;
import java.util.Map;

@Service
public class JwtTokenService {

    private static final Base64.Encoder URL_ENCODER = Base64.getUrlEncoder().withoutPadding();
    private static final Base64.Decoder URL_DECODER = Base64.getUrlDecoder();
    private final byte[] secret;
    private final long expiresSeconds;
    private final ObjectMapper objectMapper;

    public JwtTokenService(EventCenterSecurityProperties properties, ObjectMapper objectMapper) {
        // 这个构造函数的作用是读取 JWT 签名配置和 JSON 序列化器。
        this.secret = properties.getJwtSecret().getBytes(StandardCharsets.UTF_8);
        this.expiresSeconds = properties.getJwtExpiresSeconds();
        this.objectMapper = objectMapper;
    }

    public String issue(LocalUser user) {
        // 这个函数的作用是签发只包含最小身份字段的 HS256 JWT。
        try {
            long issuedAt = Instant.now().getEpochSecond();
            String header = encodeJson(Map.of("alg", "HS256", "typ", "JWT"));
            String payload = encodeJson(Map.of(
                    "sub", user.id(), "username", user.username(),
                    "iat", issuedAt, "exp", issuedAt + expiresSeconds));
            String signingInput = header + "." + payload;
            return signingInput + "." + URL_ENCODER.encodeToString(sign(signingInput));
        } catch (Exception exception) {
            throw new IllegalStateException("JWT 签发失败。", exception);
        }
    }

    public AuthenticatedUser verify(String token) {
        // 这个函数的作用是校验 JWT 结构、HMAC 签名和过期时间，并返回可信用户身份。
        try {
            String[] parts = token.split("\\.");
            if (parts.length != 3) {
                throw new IllegalArgumentException("JWT 结构无效。");
            }
            JsonNode header = objectMapper.readTree(URL_DECODER.decode(parts[0]));
            if (!"HS256".equals(header.path("alg").asText())) {
                throw new IllegalArgumentException("JWT 算法无效。");
            }
            String signingInput = parts[0] + "." + parts[1];
            if (!java.security.MessageDigest.isEqual(sign(signingInput), URL_DECODER.decode(parts[2]))) {
                throw new IllegalArgumentException("JWT 签名无效。");
            }
            JsonNode payload = objectMapper.readTree(URL_DECODER.decode(parts[1]));
            if (payload.path("exp").asLong(0) <= Instant.now().getEpochSecond()) {
                throw new IllegalArgumentException("JWT 已过期。");
            }
            String userId = payload.path("sub").asText("");
            if (userId.isBlank()) {
                throw new IllegalArgumentException("JWT 缺少用户标识。");
            }
            return new AuthenticatedUser(userId, payload.path("username").asText(""));
        } catch (IllegalArgumentException exception) {
            throw exception;
        } catch (Exception exception) {
            throw new IllegalArgumentException("JWT 无效。", exception);
        }
    }

    public long expiresSeconds() {
        // 这个函数的作用是向登录响应提供令牌有效期。
        return expiresSeconds;
    }

    private String encodeJson(Map<String, Object> value) throws Exception {
        // 这个函数的作用是把 JWT JSON 片段编码为无填充 Base64URL。
        return URL_ENCODER.encodeToString(objectMapper.writeValueAsBytes(value));
    }

    private byte[] sign(String value) throws Exception {
        // 这个函数的作用是使用服务端密钥计算 HMAC-SHA256 签名。
        Mac mac = Mac.getInstance("HmacSHA256");
        mac.init(new SecretKeySpec(secret, "HmacSHA256"));
        return mac.doFinal(value.getBytes(StandardCharsets.UTF_8));
    }
}
