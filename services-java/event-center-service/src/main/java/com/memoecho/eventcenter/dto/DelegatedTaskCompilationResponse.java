package com.memoecho.eventcenter.dto;

/** Python 任务编译图返回的受限契约；Java 仍负责校验目标会话和最终持久化。 */
public record DelegatedTaskCompilationResponse(
        boolean recognized,
        String taskType,
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
        String initialProgress,
        String stateJson
) {
}
