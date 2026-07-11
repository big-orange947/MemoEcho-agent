package com.memoecho.eventcenter.dto;

public record DraftConfirmRequest(
        String message,
        String note
) {
}
