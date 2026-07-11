package com.memoecho.eventcenter.model;

import com.memoecho.eventcenter.dto.UnifiedEventPayload;

import java.time.Instant;

public record StoredEvent(
        String eventId,
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
        Instant snoozedUntil
) {
    public static StoredEvent received(String eventId, UnifiedEventPayload payload, Instant receivedAt) {
        // 这个函数的作用是创建“刚入库，还没有完成 runtime 派发”的事件记录。
        return new StoredEvent(
                eventId,
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
                null
        );
    }

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
        // 这个函数的作用是保留原始消息和接收时间，只刷新处理链路产生的状态字段。
        return new StoredEvent(
                eventId,
                payload,
                receivedAt,
                processingStatus,
                processingSummary,
                resolvedRoute,
                writeBackStatus,
                needHumanConfirmation,
                processedAt,
                replyDraft,
                executionTrace,
                lastAction,
                lastActionNote,
                lastActionAt,
                inboxStatus,
                inboxUpdatedAt,
                snoozedUntil
        );
    }

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
        // 这个函数的作用是记录一次用户触发的草稿操作，同时保留原始事件、路由和草稿内容供审计与追溯。
        return new StoredEvent(
                eventId,
                payload,
                receivedAt,
                processingStatus,
                processingSummary,
                resolvedRoute,
                writeBackStatus,
                needHumanConfirmation,
                processedAt,
                replyDraft,
                executionTrace,
                lastAction,
                lastActionNote,
                actionAt,
                inboxStatus,
                inboxUpdatedAt,
                snoozedUntil
        );
    }

    public StoredEvent markInboxStatus(String inboxStatus, Instant snoozedUntil, Instant inboxUpdatedAt) {
        // 这个函数的作用是更新用户对消息的收件箱处理状态，不干扰 Agent 处理结果和草稿审计信息。
        return new StoredEvent(
                eventId,
                payload,
                receivedAt,
                processingStatus,
                processingSummary,
                resolvedRoute,
                writeBackStatus,
                needHumanConfirmation,
                processedAt,
                replyDraft,
                executionTrace,
                lastAction,
                lastActionNote,
                lastActionAt,
                inboxStatus,
                inboxUpdatedAt,
                snoozedUntil
        );
    }
}
