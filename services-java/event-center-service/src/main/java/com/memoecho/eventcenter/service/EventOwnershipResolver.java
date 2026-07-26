package com.memoecho.eventcenter.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.memoecho.eventcenter.dto.UnifiedEventPayload;
import com.memoecho.eventcenter.repository.PlatformConnectionRepository;
import org.springframework.stereotype.Component;

/**
 * 把连接器和桌面端产生的统一事件映射到本地登录用户。
 */
@Component
public class EventOwnershipResolver {

    private static final String FALLBACK_USER_ID = "local-user";
    private final PlatformConnectionRepository platformConnectionRepository;

    /**
     * 注入平台连接仓储，以便通过 QQ 等外部账号反查本地所有者。
     */
    public EventOwnershipResolver(PlatformConnectionRepository platformConnectionRepository) {
        this.platformConnectionRepository = platformConnectionRepository;
    }

    /**
     * 优先读取桌面事件明确携带的 userId，其次通过平台账号匹配连接所有者。
     */
    public String resolveOwnerUserId(UnifiedEventPayload event) {
        String explicitUserId = readExplicitUserId(event.rawPayload());
        if (!explicitUserId.isBlank()) {
            return explicitUserId;
        }
        return platformConnectionRepository
                .findUserIdByPlatformAndAccountId(event.platform(), event.selfId())
                .orElse(FALLBACK_USER_ID);
    }

    /**
     * 判断指定平台账号当前是否属于某个本地用户。
     * 该方法专门用于兼容早期统一归到 local-user 的事件，不读取旧载荷中的过期 userId。
     */
    public boolean isConnectedAccountOwnedBy(String userId, UnifiedEventPayload event) {
        if (userId == null || userId.isBlank() || event == null) {
            return false;
        }
        return platformConnectionRepository
                .findUserIdByPlatformAndAccountId(event.platform(), event.selfId())
                .filter(userId::equals)
                .isPresent();
    }

    /**
     * 从内部桌面事件载荷读取用户 ID，并限制长度防止异常数据进入数据库。
     */
    private String readExplicitUserId(JsonNode rawPayload) {
        if (rawPayload == null || !rawPayload.hasNonNull("userId")) {
            return "";
        }
        String value = rawPayload.path("userId").asText("").trim();
        return value.length() <= 128 ? value : value.substring(0, 128);
    }
}
