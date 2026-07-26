package com.memoecho.eventcenter.dto;

import jakarta.validation.constraints.NotBlank;

/** 用户人工发送后对指定会话代理状态的选择。 */
public record ConversationAgentStateRequest(
        @NotBlank String platform,
        @NotBlank String chatType,
        @NotBlank String chatId,
        boolean continueAgent
) {
}
