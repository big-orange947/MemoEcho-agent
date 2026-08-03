package com.memoecho.eventcenter.model;

import java.time.Instant;

/**
 * 表示用户通过自然语言交给 Memo Echo 的持续任务。
 *
 * 与一次性 Agent 命令不同，委托任务会保存目标会话、成功条件和生命周期状态，
 * 后续执行器可以安全地恢复任务，而不必重新猜测用户原始意图。
 */
public record DelegatedTask(
        String id,
        String workflowId,
        String stepKey,
        int stepOrder,
        String stepRole,
        String stepInstruction,
        String dependsOnJson,
        String requiredFactsJson,
        String producesFactsJson,
        String resultJson,
        long activationVersion,
        String userId,
        String taskType,
        String status,
        String originalCommand,
        String sourceExecutionId,
        String targetQuery,
        String platform,
        String chatType,
        String chatId,
        String targetName,
        String objective,
        String successCriteria,
        String deadlineText,
        double confidence,
        String clarificationQuestion,
        boolean requiresConfirmation,
        String executionMode,
        String progressSummary,
        String stateJson,
        String lastEventId,
        Instant startedAt,
        Instant completedAt,
        String completionReport,
        Instant createdAt,
        Instant updatedAt
) {
    /** 兼容旧解析器和测试；新任务默认采用自动完成模式，但尚未写入运行进度。 */
    public DelegatedTask(
            String id, String userId, String taskType, String status, String originalCommand,
            String targetQuery, String platform, String chatType, String chatId, String targetName,
            String objective, String successCriteria, String deadlineText, double confidence,
            String clarificationQuestion, boolean requiresConfirmation, Instant createdAt, Instant updatedAt
    ) {
        this(id, null, "", 0, "ACTION", "", "[]", "[]", "[]", "{}", 0,
                userId, taskType, status, originalCommand, null, targetQuery, platform, chatType, chatId,
                targetName, objective, successCriteria, deadlineText, confidence, clarificationQuestion,
                requiresConfirmation, "AUTO_COMPLETE", "", "{}", "", null, null, "", createdAt, updatedAt);
    }

    /**
     * 为旧的单任务创建链路提供兼容构造器。
     * 新主控台链路必须使用包含工作流字段的完整构造器。
     */
    public DelegatedTask(
            String id, String userId, String taskType, String status, String originalCommand,
            String sourceExecutionId, String targetQuery, String platform, String chatType, String chatId,
            String targetName, String objective, String successCriteria, String deadlineText, double confidence,
            String clarificationQuestion, boolean requiresConfirmation, String executionMode,
            String progressSummary, String stateJson, String lastEventId, Instant startedAt,
            Instant completedAt, String completionReport, Instant createdAt, Instant updatedAt
    ) {
        this(id, null, "", 0, "ACTION", "", "[]", "[]", "[]", "{}", 0,
                userId, taskType, status, originalCommand, sourceExecutionId, targetQuery, platform,
                chatType, chatId, targetName, objective, successCriteria, deadlineText, confidence,
                clarificationQuestion, requiresConfirmation, executionMode, progressSummary, stateJson,
                lastEventId, startedAt, completedAt, completionReport, createdAt, updatedAt);
    }
}
