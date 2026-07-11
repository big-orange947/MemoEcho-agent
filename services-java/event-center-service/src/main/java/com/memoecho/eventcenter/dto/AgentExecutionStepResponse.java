package com.memoecho.eventcenter.dto;

import java.util.List;

public record AgentExecutionStepResponse(
        String agent,
        String status,
        List<String> toolNames,
        List<String> nextActions,
        boolean needHumanConfirmation
) {
}
