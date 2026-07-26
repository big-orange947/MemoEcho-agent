package com.memoecho.eventcenter.dto;

import java.util.List;

public record ConversationMessageResponse(
        String eventId,
        String platform,
        String chatType,
        String chatId,
        String chatName,
        String senderId,
        String senderName,
        String senderRole,
        String senderAvatar,
        String text,
        String timestamp,
        List<String> mentions,
        List<AttachmentPayload> attachments,
        boolean processed,
        boolean replied,
        String route,
        String dispatchMode,
        String processingStatus,
        String processingSummary,
        String writeBackStatus,
        boolean needHumanConfirmation,
        String replyDraft,
        String inboxStatus,
        String snoozedUntil,
        String messageOrigin,
        List<MediaAnalysisResponse> mediaAnalysis,
        String actorType,
        String platformMessageId,
        String clientMessageId,
        String correlationId,
        Long sequence,
        String sentAt,
        String receivedAt,
        String importedAt,
        String direction,
        String delegatedTaskId
) {
    /** 兼容尚未传递统一消息身份的现有映射代码。 */
    public ConversationMessageResponse(
            String eventId, String platform, String chatType, String chatId, String chatName,
            String senderId, String senderName, String senderRole, String senderAvatar, String text,
            String timestamp, List<String> mentions, List<AttachmentPayload> attachments,
            boolean processed, boolean replied, String route, String dispatchMode, String processingStatus,
            String processingSummary, String writeBackStatus, boolean needHumanConfirmation, String replyDraft,
            String inboxStatus, String snoozedUntil, String messageOrigin,
            List<MediaAnalysisResponse> mediaAnalysis
    ) {
        this(eventId, platform, chatType, chatId, chatName, senderId, senderName, senderRole, senderAvatar,
                text, timestamp, mentions, attachments, processed, replied, route, dispatchMode,
                processingStatus, processingSummary, writeBackStatus, needHumanConfirmation, replyDraft,
                inboxStatus, snoozedUntil, messageOrigin, mediaAnalysis, null, null, null, null, null,
                timestamp, null, null, null, null);
    }

    /** 兼容尚未传递消息来源的旧客户端和测试数据。 */
    public ConversationMessageResponse(
            String eventId, String platform, String chatType, String chatId, String chatName,
            String senderId, String senderName, String senderRole, String text, String timestamp,
            List<String> mentions, List<AttachmentPayload> attachments, boolean processed, boolean replied,
            String route, String dispatchMode, String processingStatus, String processingSummary,
            String writeBackStatus, boolean needHumanConfirmation, String replyDraft,
            String inboxStatus, String snoozedUntil
    ) {
        this(eventId, platform, chatType, chatId, chatName, senderId, senderName, senderRole, null, text,
                timestamp, mentions, attachments, processed, replied, route, dispatchMode, processingStatus,
                processingSummary, writeBackStatus, needHumanConfirmation, replyDraft, inboxStatus,
                snoozedUntil, "EXTERNAL", List.of(), null, null, null, null, null,
                timestamp, null, null, null, null);
    }
}
