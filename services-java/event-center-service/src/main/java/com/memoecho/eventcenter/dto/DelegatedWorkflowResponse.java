package com.memoecho.eventcenter.dto;

import com.memoecho.eventcenter.model.DelegatedWorkflow;

import java.time.Instant;
import java.util.List;

/** 一条主控台命令及其全部有向依赖步骤的聚合视图。 */
public record DelegatedWorkflowResponse(
        String id,
        String sourceExecutionId,
        String originalCommand,
        String title,
        String workflowType,
        String status,
        String progressSummary,
        String failureReason,
        Instant createdAt,
        Instant updatedAt,
        Instant completedAt,
        List<DelegatedWorkflowStepResponse> steps
) {
    /** 将领域对象和步骤视图合并为 API 响应。 */
    public static DelegatedWorkflowResponse from(
            DelegatedWorkflow workflow,
            List<DelegatedWorkflowStepResponse> steps
    ) {
        return new DelegatedWorkflowResponse(
                workflow.id(), workflow.sourceExecutionId(), workflow.originalCommand(), workflow.title(),
                workflow.workflowType(), workflow.status(), workflow.progressSummary(), workflow.failureReason(),
                workflow.createdAt(), workflow.updatedAt(), workflow.completedAt(), List.copyOf(steps));
    }
}
