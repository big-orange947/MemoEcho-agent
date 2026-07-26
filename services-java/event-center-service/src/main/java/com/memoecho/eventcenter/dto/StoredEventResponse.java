package com.memoecho.eventcenter.dto;

public record StoredEventResponse(
        String eventId,
        String platform,
        String eventType,
        String chatType,
        String chatId,
        String text,
        String timestamp,
        String receivedAt,
        String processingStatus,
        String processingSummary,
        String resolvedRoute,
        String writeBackStatus,
        boolean needHumanConfirmation,
        String processedAt,
        String replyDraft,
        ExecutionTraceResponse executionTrace,
        String lastAction,
        String lastActionNote,
        String lastActionAt,
        String inboxStatus,
        String inboxUpdatedAt,
        String snoozedUntil,
        String messageOrigin
) {
    /** 兼容旧的事件详情构造代码，旧数据默认按外部来源展示。 */
    public StoredEventResponse(
            String eventId, String platform, String eventType, String chatType, String chatId, String text,
            String timestamp, String receivedAt, String processingStatus, String processingSummary,
            String resolvedRoute, String writeBackStatus, boolean needHumanConfirmation, String processedAt,
            String replyDraft, ExecutionTraceResponse executionTrace, String lastAction, String lastActionNote,
            String lastActionAt, String inboxStatus, String inboxUpdatedAt, String snoozedUntil
    ) {
        this(eventId, platform, eventType, chatType, chatId, text, timestamp, receivedAt, processingStatus,
                processingSummary, resolvedRoute, writeBackStatus, needHumanConfirmation, processedAt,
                replyDraft, executionTrace, lastAction, lastActionNote, lastActionAt, inboxStatus,
                inboxUpdatedAt, snoozedUntil, "EXTERNAL");
    }
}
