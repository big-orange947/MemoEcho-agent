package com.memoecho.eventcenter.dto;

import java.time.Instant;

/**
 * 面向桌面端的安全资产元数据，刻意不包含资产正文和数据库密文。
 */
public record SecureAssetResponse(
        String id,
        String name,
        String type,
        String description,
        String contentType,
        String usagePolicy,
        Integer remainingUses,
        boolean enabled,
        boolean contentConfigured,
        Instant createdAt,
        Instant updatedAt,
        Instant lastUsedAt
) {
}
