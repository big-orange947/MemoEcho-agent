package com.memoecho.eventcenter.dto;

import jakarta.validation.constraints.NotBlank;

public record UserModelProfileResolveRequest(
        String userId,
        @NotBlank String route,
        String profileId
) {
}
