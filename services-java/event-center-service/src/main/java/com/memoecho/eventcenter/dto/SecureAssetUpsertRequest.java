package com.memoecho.eventcenter.dto;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

/**
 * 创建或更新安全资产时使用的请求体。
 *
 * <p>更新时 content 允许为 null，表示保留已有正文；空字符串表示主动清空正文。</p>
 */
public record SecureAssetUpsertRequest(
        @NotBlank @Size(max = 255) String name,
        @NotBlank @Size(max = 64) String type,
        @Size(max = 2000) String description,
        @Size(max = 255) String contentType,
        @Size(max = 2_000_000) String content,
        @Size(max = 32) String usagePolicy,
        Integer remainingUses,
        Boolean enabled
) {
}
