package com.memoecho.eventcenter.dto;

/** 用户对结束代理申请作出的决定；拒绝后任务恢复为进行中。 */
public record ConversationProxyTaskDecisionRequest(
        String chatId,
        boolean approved
) {
}
