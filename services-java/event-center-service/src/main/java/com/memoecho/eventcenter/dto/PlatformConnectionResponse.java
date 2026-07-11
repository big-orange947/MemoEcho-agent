package com.memoecho.eventcenter.dto;

/**
 * 工作台展示的平台连接状态，不包含 Token、Cookie 等凭据。
 */
public record PlatformConnectionResponse(
        String id,
        String userId,
        String name,
        String platform,
        String connector,
        boolean enabled,
        boolean connected,
        String accountId,
        String accountName,
        String health,
        String message,
        String lastCheckedAt,
        boolean hasCredential
) {
}
