package com.memoecho.eventcenter.dto;

public record WorkspaceSuggestedActionResponse(
        String type,
        String title,
        String reason,
        String targetId
) {
}
