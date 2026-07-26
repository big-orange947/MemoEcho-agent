package com.memoecho.eventcenter.dto;

import java.util.List;

/**
 * 桌面端打开上下文时返回的按需快照：同一次请求同时得到摘要和双方真实消息。
 */
public record ConversationProgressResponse(
        String summary,
        boolean generatedByModel,
        String generatedAt,
        boolean summaryUpdated,
        String latestAgentEventId,
        List<ConversationMessageResponse> messages
) {
}
