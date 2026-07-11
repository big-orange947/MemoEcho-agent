package com.memoecho.eventcenter.dto;

import jakarta.validation.constraints.NotBlank;

import java.util.List;

public record UserModelProfileUpsertRequest(
        String userId,
        @NotBlank String name,
        String description,
        Boolean enabled,
        String provider,
        String baseUrl,
        String apiKey,
        Boolean clearApiKey,
        String model,
        Double temperature,
        Integer maxTokens,
        List<String> supportedRoutes,
        Boolean isDefault,
        Integer priority
) {
}
