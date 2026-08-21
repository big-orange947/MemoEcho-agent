package com.memoecho.eventcenter.model;

import java.time.Instant;

/**
 * 主控台对话线程内的一条消息。
 *
 * role 取值：user / agent / system。agent 消息通过 executionId（= 命令 commandId）
 * 关联一次主控台命令，并可选引用该命令产生的委托任务与父工作流。
 */
public record WorkspaceThreadMessage(
        String id,
        String threadId,
        String userId,
        String role,
        String content,
        String status,
        String executionId,
        String taskId,
        String workflowId,
        String resultJson,
        Instant createdAt
) {
}