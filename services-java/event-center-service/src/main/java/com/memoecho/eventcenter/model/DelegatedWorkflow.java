package com.memoecho.eventcenter.model;

import java.time.Instant;

/**
 * 表示一次主控台命令对应的父工作流。
 *
 * 父工作流保存原始命令、完整执行计划和跨步骤共享事实；具体外部动作由关联的
 * {@link DelegatedTask} 步骤执行，避免多个联系人任务彼此看不到进度。
 */
public record DelegatedWorkflow(
        String id,
        String userId,
        String sourceExecutionId,
        String originalCommand,
        String title,
        String workflowType,
        String status,
        String planJson,
        String factsJson,
        String progressSummary,
        String failureReason,
        Instant createdAt,
        Instant updatedAt,
        Instant completedAt
) {
}

