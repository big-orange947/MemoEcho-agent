package com.memoecho.eventcenter.model;

import java.time.Instant;
import java.util.List;

public record UserModelProfile(
        String id,
        String userId,
        String name,
        String description,
        boolean enabled,
        String provider,
        String baseUrl,
        String apiKey,
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
