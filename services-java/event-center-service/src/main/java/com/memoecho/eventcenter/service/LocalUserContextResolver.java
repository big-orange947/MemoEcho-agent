package com.memoecho.eventcenter.service;

import com.memoecho.eventcenter.config.EventCenterSecurityProperties;
import com.memoecho.eventcenter.repository.LocalUserRepository;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Component;
import org.springframework.web.server.ResponseStatusException;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;

@Component
public class LocalUserContextResolver {

    private static final String DEFAULT_LOCAL_USER = "local-user";
    private final JwtTokenService jwtTokenService;
    private final boolean allowLegacyUserHeader;
    private final String runtimeToken;
    private final LocalUserRepository userRepository;

    public LocalUserContextResolver(
            JwtTokenService jwtTokenService,
            EventCenterSecurityProperties properties,
            LocalUserRepository userRepository
    ) {
        // 这个构造函数的作用是注入 JWT 校验器和开发期兼容开关。
        this.jwtTokenService = jwtTokenService;
        this.allowLegacyUserHeader = properties.isAllowLegacyUserHeader();
        this.runtimeToken = properties.getRuntimeToken();
        this.userRepository = userRepository;
    }

    public String resolve(String authorization, String requestedUserId) {
        // 这个函数的作用是优先从 Bearer Token 获取可信用户 ID，无令牌时按配置决定是否允许旧用户头。
        if (authorization != null && !authorization.isBlank()) {
            if (!authorization.startsWith("Bearer ")) {
                throw new ResponseStatusException(HttpStatus.UNAUTHORIZED, "Authorization 必须使用 Bearer Token。");
            }
            try {
                String userId = jwtTokenService.verify(authorization.substring(7).trim()).userId();
                return userRepository.findById(userId)
                        .filter(user -> user.enabled())
                        .map(user -> user.id())
                        .orElseThrow(() -> new IllegalArgumentException("JWT 用户不存在或已停用。"));
            } catch (IllegalArgumentException exception) {
                throw new ResponseStatusException(HttpStatus.UNAUTHORIZED, "登录令牌无效或已过期。", exception);
            }
        }
        if (!allowLegacyUserHeader) {
            throw new ResponseStatusException(HttpStatus.UNAUTHORIZED, "请先登录。");
        }
        return resolve(requestedUserId);
    }

    public String resolve(String requestedUserId) {
        // 这个函数的作用是统一解析开发期用户上下文；未来接入 JWT 后由这里改为读取认证主体。
        if (requestedUserId == null || requestedUserId.isBlank()) {
            return DEFAULT_LOCAL_USER;
        }
        String normalized = requestedUserId.trim();
        return normalized.length() <= 128 ? normalized : normalized.substring(0, 128);
    }

    /**
     * 校验 Python runtime 的服务令牌，并确认其代表的用户真实存在且未被停用。
     */
    public String resolveRuntimeUser(String providedRuntimeToken, String requestedUserId) {
        if (runtimeToken.isBlank()) {
            throw new ResponseStatusException(HttpStatus.SERVICE_UNAVAILABLE, "未配置 runtime 服务令牌。");
        }
        if (providedRuntimeToken == null || !MessageDigest.isEqual(
                runtimeToken.getBytes(StandardCharsets.UTF_8),
                providedRuntimeToken.getBytes(StandardCharsets.UTF_8)
        )) {
            throw new ResponseStatusException(HttpStatus.UNAUTHORIZED, "runtime 服务令牌无效。");
        }
        String userId = resolve(requestedUserId);
        return userRepository.findById(userId)
                .filter(user -> user.enabled())
                .map(user -> user.id())
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.UNAUTHORIZED, "runtime 目标用户不存在或已停用。"));
    }
}
