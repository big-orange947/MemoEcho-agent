package com.memoecho.eventcenter.dto;

import com.fasterxml.jackson.annotation.JsonFormat;

import java.time.LocalDateTime;

public record TaskServiceTaskResponse(
        String id,
        String sourceEventId,
        String platform,
        String chatId,
        String senderId,
        String title,
        String description,
        @JsonFormat(pattern = "yyyy-MM-dd HH:mm:ss")
        LocalDateTime dueTime,
        String priority,
        String status,
        String confidence,
        @JsonFormat(pattern = "yyyy-MM-dd HH:mm:ss")
        LocalDateTime createdAt
) {
}
