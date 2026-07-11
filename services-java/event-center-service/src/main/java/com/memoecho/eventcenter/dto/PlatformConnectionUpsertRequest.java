package com.memoecho.eventcenter.dto;

import jakarta.validation.constraints.NotBlank;

/**
 * 创建或更新平台连接的请求。credential 是只写字段，保存后不会通过任何响应返回。
 */
public record PlatformConnectionUpsertRequest(
        @NotBlank String name,
        @NotBlank String platform,
        @NotBlank String connector,
        Boolean enabled,
        String connectorBaseUrl,
        String credential
) {
}
