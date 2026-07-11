package com.memoecho.eventcenter.dto;

public record AuthTokenResponse(
        String tokenType,
        String accessToken,
        long expiresIn,
        String userId,
        String username,
        String displayName
) {
}
