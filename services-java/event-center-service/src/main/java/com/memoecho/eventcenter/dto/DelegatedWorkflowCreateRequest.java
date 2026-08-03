package com.memoecho.eventcenter.dto;

import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotEmpty;

import java.util.List;

/** Python Planner 编译完成后提交的父工作流创建契约。 */
public record DelegatedWorkflowCreateRequest(
        @NotBlank String command,
        String executionId,
        @NotBlank String title,
        String workflowType,
        @NotEmpty List<@Valid DelegatedWorkflowStepCreateRequest> steps
) {
}
