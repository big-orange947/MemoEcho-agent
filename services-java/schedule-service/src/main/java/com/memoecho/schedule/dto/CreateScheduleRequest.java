package com.memoecho.schedule.dto;

import com.fasterxml.jackson.annotation.JsonFormat;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;

import java.time.LocalDateTime;

public record CreateScheduleRequest(
        @NotBlank String sourceEventId,
        @NotBlank String platform,
        @NotBlank String chatId,
        @NotBlank String senderId,
        @NotBlank String title,
        @JsonFormat(pattern = "yyyy-MM-dd HH:mm:ss")
        @NotNull LocalDateTime startTime,
        @JsonFormat(pattern = "yyyy-MM-dd HH:mm:ss")
        LocalDateTime endTime,
        String location,
        @NotBlank String content,
        String participants,
        String confidence
) {
}

