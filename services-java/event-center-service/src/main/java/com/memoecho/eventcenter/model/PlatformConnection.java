package com.memoecho.eventcenter.model;

import java.time.Instant;

/**
 * 用户拥有的平台连接档案。credentialCiphertext 只允许在服务内部使用，不能映射到 API 响应。
 */
public record PlatformConnection(
        String id,
        String userId,
        String name,
        String platform,
        String connector,
        boolean enabled,
        String connectorBaseUrl,
        String credentialCiphertext,
        String accountId,
        String accountName,
        String health,
        String healthMessage,
        Instant lastCheckedAt,
        Instant createdAt,
        Instant updatedAt
) {
}
