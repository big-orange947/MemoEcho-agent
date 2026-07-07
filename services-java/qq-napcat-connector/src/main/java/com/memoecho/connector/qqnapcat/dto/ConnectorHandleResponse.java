package com.memoecho.connector.qqnapcat.dto;

public record ConnectorHandleResponse(
        UnifiedEventPayload unifiedEvent,
        EventCenterResponse eventCenter,
        String message
) {
}
