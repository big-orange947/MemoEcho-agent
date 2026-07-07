package com.memoecho.eventcenter.dto;

public record EventIngestResponse(
        String eventId,
        boolean accepted,
        boolean duplicate,
        DispatchResult dispatch,
        String message
) {
}
