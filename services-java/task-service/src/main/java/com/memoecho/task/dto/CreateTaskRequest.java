package com.memoecho.task.dto;

import com.fasterxml.jackson.annotation.JsonFormat;
import jakarta.validation.constraints.NotBlank;

import java.time.LocalDateTime;

public record CreateTaskRequest(
        @NotBlank String sourceEventId,
        @NotBlank String platform,
        @NotBlank String chatId,
        @NotBlank String senderId,
        @NotBlank String title,
        @NotBlank String description,
        @JsonFormat(pattern = "yyyy-MM-dd HH:mm:ss")
        LocalDateTime dueTime,
        @NotBlank String priority,
        @NotBlank String status,
        String confidence
) {
}
