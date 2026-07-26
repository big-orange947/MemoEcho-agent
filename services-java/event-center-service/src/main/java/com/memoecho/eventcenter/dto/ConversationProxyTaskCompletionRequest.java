package com.memoecho.eventcenter.dto;

import java.util.List;

/** Runtime 在确认成功条件已经满足后提交的结束代理申请。 */
public record ConversationProxyTaskCompletionRequest(
        String chatId,
        String summary,
        String reason,
        List<String> evidence
) {
}
