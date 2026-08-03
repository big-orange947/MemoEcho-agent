package com.memoecho.eventcenter.dto;

import com.memoecho.eventcenter.model.DelegatedTask;

import java.time.Instant;
import java.util.List;

/** 客户端展示父工作流时间线所需的步骤视图。 */
public record DelegatedWorkflowStepResponse(
        String taskId,
        String stepKey,
        int order,
        String role,
        String instruction,
        List<String> dependsOn,
        List<String> requiredFacts,
        List<String> producesFacts,
        String status,
        String targetName,
        String platform,
        String chatType,
        String chatId,
        String objective,
        String progressSummary,
        Instant startedAt,
        Instant completedAt
) {
}
