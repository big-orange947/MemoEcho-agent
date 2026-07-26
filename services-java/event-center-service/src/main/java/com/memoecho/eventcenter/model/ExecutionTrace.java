package com.memoecho.eventcenter.model;

import java.util.List;

public record ExecutionTrace(
        String executionId,
        String route,
        String summary,
        List<String> writeBackActions,
        List<AgentExecutionStep> steps,
        NotificationDecision notification,
        List<String> verifiedMemoryIds
) {
    /**
     * 这个规范构造函数的作用是把旧版 JSON 中缺失的记忆审计字段归一为空列表。
     */
    public ExecutionTrace {
        verifiedMemoryIds = verifiedMemoryIds == null ? List.of() : List.copyOf(verifiedMemoryIds);
    }

    /**
     * 兼容早期不含通知决策的执行轨迹数据和测试构造代码。
     */
    public ExecutionTrace(
            String executionId,
            String route,
            String summary,
            List<String> writeBackActions,
            List<AgentExecutionStep> steps
    ) {
        this(executionId, route, summary, writeBackActions, steps, null, List.of());
    }

    /**
     * 兼容已经包含通知决策、但尚未包含长期记忆审计字段的调用方和历史数据。
     */
    public ExecutionTrace(
            String executionId,
            String route,
            String summary,
            List<String> writeBackActions,
            List<AgentExecutionStep> steps,
            NotificationDecision notification
    ) {
        this(executionId, route, summary, writeBackActions, steps, notification, List.of());
    }
}
