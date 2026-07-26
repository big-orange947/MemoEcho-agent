package com.memoecho.eventcenter.dto;

import java.util.List;

public record ExecutionTraceResponse(
        String executionId,
        String route,
        String summary,
        List<String> writeBackActions,
        List<AgentExecutionStepResponse> steps,
        NotificationDecisionResponse notification,
        List<String> verifiedMemoryIds
) {
    /**
     * 这个规范构造函数的作用是保证 API 始终返回数组，避免旧数据向客户端暴露 null。
     */
    public ExecutionTraceResponse {
        verifiedMemoryIds = verifiedMemoryIds == null ? List.of() : List.copyOf(verifiedMemoryIds);
    }

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
        this(executionId, route, summary, writeBackActions, steps, null, List.of());
    }

    /**
     * 兼容已经包含通知决策、但尚未包含长期记忆审计字段的调用方。
     */
    public ExecutionTraceResponse(
            String executionId,
            String route,
            String summary,
            List<String> writeBackActions,
            List<AgentExecutionStepResponse> steps,
            NotificationDecisionResponse notification
    ) {
        this(executionId, route, summary, writeBackActions, steps, notification, List.of());
    }
}
