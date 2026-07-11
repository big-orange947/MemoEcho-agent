package com.memoecho.eventcenter.dto;

public record QqMessageSendResult(
        boolean successful,
        String summary
) {
}
