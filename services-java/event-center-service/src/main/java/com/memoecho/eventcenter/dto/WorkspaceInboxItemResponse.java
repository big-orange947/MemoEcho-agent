package com.memoecho.eventcenter.dto;

public record WorkspaceInboxItemResponse(
        String eventId,
        String platform,
        String chatType,
        String chatId,
        String chatName,
        String senderId,
        String senderName,
        String text,
        String timestamp,
        String route,
        String processingStatus,
        String writeBackStatus,
        String replyDraft,
        boolean needHumanConfirmation,
        boolean actionRequired,
        String inboxStatus,
        String snoozedUntil,
        String lastAction,
        String lastActionAt,
        NotificationDecisionResponse notification
) {
    /**
     * 兼容已接入收件箱接口的旧调用方，尚未产生通知决策时返回空值。
     */
    public WorkspaceInboxItemResponse(
            String eventId,
            String platform,
            String chatType,
            String chatId,
            String chatName,
            String senderId,
            String senderName,
            String text,
            String timestamp,
            String route,
            String processingStatus,
            String writeBackStatus,
            String replyDraft,
            boolean needHumanConfirmation,
            boolean actionRequired,
            String inboxStatus,
            String snoozedUntil,
            String lastAction,
            String lastActionAt
    ) {
        this(
                eventId,
                platform,
                chatType,
                chatId,
                chatName,
                senderId,
                senderName,
                text,
                timestamp,
                route,
                processingStatus,
                writeBackStatus,
                replyDraft,
                needHumanConfirmation,
                actionRequired,
                inboxStatus,
                snoozedUntil,
                lastAction,
                lastActionAt,
                null
        );
    }
}
