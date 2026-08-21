package com.memoecho.eventcenter.dto;

import com.memoecho.eventcenter.model.WorkspaceThreadMessage;

import java.time.Instant;

/** 主控台对话线程内一条消息的稳定响应。 */
public record WorkspaceThreadMessageResponse(
        String id,
        String threadId,
        String role,
        String content,
        String status,
        String executionId,
        String taskId,
        String workflowId,
        String resultJson,
        Instant createdAt
) {
    public static WorkspaceThreadMessageResponse from(WorkspaceThreadMessage message) {
        return new WorkspaceThreadMessageResponse(
                message.id(), message.threadId(), message.role(), message.content(), message.status(),
                message.executionId(), message.taskId(), message.workflowId(), message.resultJson(),
                message.createdAt());
    }
}