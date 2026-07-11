package com.memoecho.eventcenter.dto;

import jakarta.validation.constraints.NotBlank;

public record ConversationProfileMatchRequest(
        @NotBlank String platform,
        String accountId,
        String scene,
        @NotBlank String chatType,
        @NotBlank String chatId,
        String senderId,
        String senderRole,
        String route,
        String text,
        Boolean atSelf
) {
}
