package com.memoecho.eventcenter.dto;

import jakarta.validation.constraints.Size;

/** 用户拒绝候选记忆时携带的可选原因，用于解释为什么该事实不能再次进入上下文。 */
public record MemoryCandidateRejectRequest(
        @Size(max = 2000) String reason
) {
}
