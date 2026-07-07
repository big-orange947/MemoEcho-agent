package com.memoecho.task.model;

import java.time.LocalDateTime;

public record TaskItem(
        String id,
        String sourceEventId,
        String platform,
        String chatId,
        String senderId,
        String title,
        String description,
        LocalDateTime dueTime,
        String priority,
        String status,
        String confidence,
        LocalDateTime createdAt
) {
}
