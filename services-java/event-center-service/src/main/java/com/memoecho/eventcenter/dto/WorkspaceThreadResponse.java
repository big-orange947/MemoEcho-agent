package com.memoecho.eventcenter.dto;

import com.memoecho.eventcenter.model.WorkspaceThread;

import java.time.Instant;

/** 主控台对话线程的稳定响应。 */
public record WorkspaceThreadResponse(
        String id,
        String userId,
        String title,
        boolean pinned,
        boolean archived,
        Instant createdAt,
        Instant updatedAt
) {
    public static WorkspaceThreadResponse from(WorkspaceThread thread) {
        return new WorkspaceThreadResponse(
                thread.id(), thread.userId(), thread.title(), thread.pinned(), thread.archived(),
                thread.createdAt(), thread.updatedAt());
    }
}