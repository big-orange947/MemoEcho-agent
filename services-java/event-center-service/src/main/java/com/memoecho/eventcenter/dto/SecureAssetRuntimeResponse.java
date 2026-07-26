package com.memoecho.eventcenter.dto;

import java.time.Instant;

/**
 * 仅供受信任 Agent Runtime 使用的资产解析结果。
 *
 * <p>content 是解密后的敏感正文，因此该 DTO 不得从普通用户控制器返回。</p>
 */
public record SecureAssetRuntimeResponse(
        String id,
        String name,
        String type,
        String description,
        String contentType,
        String content,
        String usagePolicy,
        Integer remainingUses,
        Instant resolvedAt
) {
}
