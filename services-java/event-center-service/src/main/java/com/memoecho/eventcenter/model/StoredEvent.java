package com.memoecho.eventcenter.model;

import com.memoecho.eventcenter.dto.UnifiedEventPayload;

import java.time.Instant;

public record StoredEvent(
        String eventId,
        UnifiedEventPayload payload,
        Instant receivedAt
) {
}
