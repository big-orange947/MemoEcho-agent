package com.memoecho.eventcenter.dto;

import jakarta.validation.constraints.NotBlank;

/** 用户处理候选记忆与已确认记忆冲突时提交的明确决策。 */
public record MemoryConflictResolutionRequest(
        @NotBlank String decision
) {
}
