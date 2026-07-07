package com.memoecho.schedule.model;

import java.time.LocalDateTime;

public record ScheduleItem(
        String id,
        String sourceEventId,
        String platform,
        String chatId,
        String senderId,
        String title,
        LocalDateTime startTime,
        LocalDateTime endTime,
        String location,
        String content,
        String participants,
        String confidence,
        LocalDateTime createdAt
) {
}

