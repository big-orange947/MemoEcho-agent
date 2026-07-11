package com.memoecho.eventcenter.dto;

import com.fasterxml.jackson.annotation.JsonFormat;

import java.time.LocalDateTime;

public record WorkspaceTaskDigestResponse(
        String id,
        String title,
        String description,
        String priority,
        String status,
        @JsonFormat(pattern = "yyyy-MM-dd HH:mm:ss")
        LocalDateTime dueTime
) {
}
