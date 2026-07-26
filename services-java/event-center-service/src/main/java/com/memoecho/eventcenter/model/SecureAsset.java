package com.memoecho.eventcenter.model;

import java.time.Instant;

/**
 * 安全资产仓库中的一条资产记录。
 *
 * <p>payloadCiphertext 永远保存 AES-GCM 密文；普通用户接口只会把它转换成
 * contentConfigured，不会把密文或明文发送到桌面端。</p>
 */
public record SecureAsset(
        String id,
        String userId,
        String name,
        String type,
        String description,
        String contentType,
        String payloadCiphertext,
        String usagePolicy,
        Integer remainingUses,
        boolean enabled,
        Instant createdAt,
        Instant updatedAt,
        Instant lastUsedAt
) {
}
