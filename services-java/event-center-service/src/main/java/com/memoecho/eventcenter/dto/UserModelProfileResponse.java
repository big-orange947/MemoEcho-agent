package com.memoecho.eventcenter.dto;

import java.time.Instant;
import java.util.List;

public record UserModelProfileResponse(
        String id,
        String userId,
        String name,
        String description,
        boolean enabled,
        String provider,
        String baseUrl,
        boolean hasApiKey,
        String apiKeyMasked,
        String model,
        Double temperature,
        Integer maxTokens,
        List<String> supportedRoutes,
        boolean isDefault,
        int priority,
        Instant createdAt,
        Instant updatedAt
) {
}
