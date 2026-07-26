package com.memoecho.eventcenter.model;

import com.memoecho.eventcenter.dto.UnifiedEventPayload;

import java.time.Instant;

/**
 * 保存统一事件、Agent 处理结果以及用户在收件箱中的操作状态。
 */
public record StoredEvent(
        String eventId,
        String ownerUserId,
        UnifiedEventPayload payload,
        Instant receivedAt,
        String processingStatus,
        String processingSummary,
        String resolvedRoute,
        String writeBackStatus,
        boolean needHumanConfirmation,
        Instant processedAt,
        String replyDraft,
        ExecutionTrace executionTrace,
        String lastAction,
        String lastActionNote,
        Instant lastActionAt,
        String inboxStatus,
        Instant inboxUpdatedAt,
        Instant snoozedUntil,
        String messageOrigin
) {
    /** 兼容旧调用；未传来源的历史事件一律按外部消息处理。 */
    public StoredEvent(
            String eventId, String ownerUserId, UnifiedEventPayload payload, Instant receivedAt,
            String processingStatus, String processingSummary, String resolvedRoute, String writeBackStatus,
            boolean needHumanConfirmation, Instant processedAt, String replyDraft, ExecutionTrace executionTrace,
            String lastAction, String lastActionNote, Instant lastActionAt, String inboxStatus,
            Instant inboxUpdatedAt, Instant snoozedUntil
    ) {
        this(eventId, ownerUserId, payload, receivedAt, processingStatus, processingSummary, resolvedRoute,
                writeBackStatus, needHumanConfirmation, processedAt, replyDraft, executionTrace, lastAction,
                lastActionNote, lastActionAt, inboxStatus, inboxUpdatedAt, snoozedUntil, "EXTERNAL");
    }

    /**
     * 创建带明确用户归属的新事件。
     */
    public static StoredEvent received(
            String eventId,
            String ownerUserId,
            UnifiedEventPayload payload,
            Instant receivedAt
    ) {
        return new StoredEvent(
                eventId,
                ownerUserId,
                payload,
                receivedAt,
                "RECEIVED",
                "事件已进入 event-center，等待派发给 Agent Runtime。",
                "",
                "PENDING",
                false,
                null,
                "",
                null,
                "RECEIVED",
                "",
                null,
                "NEW",
                receivedAt,
                null,
                "EXTERNAL"
        );
    }

    /**
     * 兼容旧调用方；未提供归属时将事件放入本地兼容用户空间。
     */
    public static StoredEvent received(String eventId, UnifiedEventPayload payload, Instant receivedAt) {
        return received(eventId, "local-user", payload, receivedAt);
    }

    /**
     * 写入 Runtime 处理结果，同时保留事件归属和收件箱状态。
     */
    public StoredEvent markProcessed(
            String processingStatus,
            String processingSummary,
            String resolvedRoute,
            String writeBackStatus,
            boolean needHumanConfirmation,
            Instant processedAt,
            String replyDraft,
            ExecutionTrace executionTrace
    ) {
        return new StoredEvent(
                eventId, ownerUserId, payload, receivedAt, processingStatus, processingSummary, resolvedRoute,
                writeBackStatus, needHumanConfirmation, processedAt, replyDraft, executionTrace, lastAction,
                lastActionNote, lastActionAt, inboxStatus, inboxUpdatedAt, snoozedUntil, messageOrigin
        );
    }

    /**
     * 记录用户对草稿执行的确认、拒绝或重试操作。
     */
    public StoredEvent markAction(
            String processingStatus,
            String processingSummary,
            String writeBackStatus,
            boolean needHumanConfirmation,
            String replyDraft,
            String lastAction,
            String lastActionNote,
            Instant actionAt
    ) {
        return new StoredEvent(
                eventId, ownerUserId, payload, receivedAt, processingStatus, processingSummary, resolvedRoute,
                writeBackStatus, needHumanConfirmation, processedAt, replyDraft, executionTrace, lastAction,
                lastActionNote, actionAt, inboxStatus, inboxUpdatedAt, snoozedUntil, messageOrigin
        );
    }

    /**
     * 更新收件箱状态，不改变 Agent 处理结果和草稿审计信息。
     */
    public StoredEvent markInboxStatus(String inboxStatus, Instant snoozedUntil, Instant inboxUpdatedAt) {
        return new StoredEvent(
                eventId, ownerUserId, payload, receivedAt, processingStatus, processingSummary, resolvedRoute,
                writeBackStatus, needHumanConfirmation, processedAt, replyDraft, executionTrace, lastAction,
                lastActionNote, lastActionAt, inboxStatus, inboxUpdatedAt, snoozedUntil, messageOrigin
        );
    }

    /** 为入站消息标注来源，供后续历史过滤与个人风格提炼安全筛选。 */
    public StoredEvent withMessageOrigin(String messageOrigin) {
        return new StoredEvent(
                eventId, ownerUserId, payload, receivedAt, processingStatus, processingSummary, resolvedRoute,
                writeBackStatus, needHumanConfirmation, processedAt, replyDraft, executionTrace, lastAction,
                lastActionNote, lastActionAt, inboxStatus, inboxUpdatedAt, snoozedUntil,
                messageOrigin == null || messageOrigin.isBlank() ? "EXTERNAL" : messageOrigin
        );
    }

    /**
     * 替换统一事件载荷，同时保留处理状态、草稿、收件箱和来源信息。
     * 附件异步任务只会追加分析结果到 rawPayload，因此不能重置主处理链路的状态。
     */
    public StoredEvent withPayload(UnifiedEventPayload payload) {
        return new StoredEvent(
                eventId, ownerUserId, payload, receivedAt, processingStatus, processingSummary, resolvedRoute,
                writeBackStatus, needHumanConfirmation, processedAt, replyDraft, executionTrace, lastAction,
                lastActionNote, lastActionAt, inboxStatus, inboxUpdatedAt, snoozedUntil, messageOrigin
        );
    }
}
