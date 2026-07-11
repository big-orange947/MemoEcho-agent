package com.memoecho.eventcenter.dto;

public record ConversationProfileMatchResponse(
        boolean matched,
        boolean active,
        String reason,
        ConversationProfileResponse profile
) {
}
