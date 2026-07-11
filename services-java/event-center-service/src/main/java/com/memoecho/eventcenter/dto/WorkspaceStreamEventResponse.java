package com.memoecho.eventcenter.dto;

/**
 * SSE 工作台流中发送的轻量更新事件。
 *
 * 前端收到后可按 eventId 刷新单张收件箱卡片，避免在流中重复传输完整历史消息。
 */
public record WorkspaceStreamEventResponse(
        String type,
        String eventId,
        String platform,
        String accountId,
        String chatType,
        String chatId,
        String processingStatus,
        String inboxStatus,
        boolean actionRequired,
        String occurredAt
) {
}
