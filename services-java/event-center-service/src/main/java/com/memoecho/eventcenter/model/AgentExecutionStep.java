package com.memoecho.eventcenter.model;

import java.util.List;

public record AgentExecutionStep(
        String agent,
        String status,
        List<String> toolNames,
        List<String> nextActions,
        boolean needHumanConfirmation
) {
}
