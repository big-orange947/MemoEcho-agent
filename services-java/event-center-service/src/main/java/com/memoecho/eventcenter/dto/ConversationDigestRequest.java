package com.memoecho.eventcenter.dto;

import jakarta.validation.constraints.NotBlank;

import java.util.List;

/**
 * Python Runtime 在慢通道窗口到期后提交的会话摘要。
 */
public record ConversationDigestRequest(
        @NotBlank String platform,
        String scene,
        @NotBlank String chatType,
        String chatId,
        String selfId,
        @NotBlank String aggregationKey,
        List<String> sourceEventIds,
        Integer messageCount,
        @NotBlank String summary
) {
}
