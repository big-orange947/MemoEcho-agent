package com.memoecho.eventcenter.dto;

import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;

import java.util.List;

/** Runtime 提交的单个 DAG 步骤。 */
public record DelegatedWorkflowStepCreateRequest(
        @NotBlank String stepKey,
        int order,
        String role,
        @NotBlank String instruction,
        List<String> dependsOn,
        List<String> requiredFacts,
        List<String> producesFacts,
        @NotNull @Valid DelegatedTaskCompilationResponse compilation,
        String startEventId
) {
}
