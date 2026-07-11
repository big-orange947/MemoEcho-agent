package com.memoecho.eventcenter.dto;

import java.time.Instant;

public record SnoozeEventRequest(
        Instant snoozedUntil
) {
}
