package com.memoecho.eventcenter.model;

import java.time.Instant;

/**
 * 主控台对话式工作区的线程（对话容器）。
 *
 * 线程只负责按主题组织消息，委托任务/父工作流独立运行，线程与其引用关系
 * 通过 {@link WorkspaceThreadMessage} 的 taskId/workflowId 表达。
 */
public record WorkspaceThread(
        String id,
        String userId,
        String title,
        boolean pinned,
        boolean archived,
        Instant createdAt,
        Instant updatedAt
) {
}