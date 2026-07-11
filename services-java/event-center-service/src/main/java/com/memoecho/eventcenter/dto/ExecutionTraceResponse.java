package com.memoecho.eventcenter.dto;

import java.util.List;

public record ExecutionTraceResponse(
        String executionId,
        String route,
        String summary,
        List<String> writeBackActions,
        List<AgentExecutionStepResponse> steps,
        NotificationDecisionResponse notification
) {
    /**
     * 兼容早期不含通知决策字段的调用方。
     */
    public ExecutionTraceResponse(
            String executionId,
            String route,
            String summary,
            List<String> writeBackActions,
            List<AgentExecutionStepResponse> steps
    ) {
        this(executionId, route, summary, writeBackActions, steps, null);
    }
}
