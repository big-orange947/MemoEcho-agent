package com.memoecho.eventcenter.model;

import java.util.List;

public record ExecutionTrace(
        String executionId,
        String route,
        String summary,
        List<String> writeBackActions,
        List<AgentExecutionStep> steps,
        NotificationDecision notification
) {
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
        this(executionId, route, summary, writeBackActions, steps, null);
    }
}
