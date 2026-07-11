package com.memoecho.eventcenter.model;

import java.time.Instant;

/** 本地账户领域对象，passwordHash 永远不能映射到 API 响应。 */
public record LocalUser(
        String id,
        String username,
        String displayName,
        String passwordHash,
        boolean enabled,
        Instant createdAt,
        Instant updatedAt
) {
}
