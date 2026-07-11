package com.memoecho.eventcenter.dto;

import java.util.List;

public record UserModelProfileResolvedResponse(
        String id,
        String userId,
        String name,
        String provider,
        String baseUrl,
        String apiKey,
        String model,
        Double temperature,
        Integer maxTokens,
        List<String> supportedRoutes,
        boolean isDefault,
        int priority
) {
}
