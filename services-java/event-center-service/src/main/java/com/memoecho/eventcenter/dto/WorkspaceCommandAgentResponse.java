package com.memoecho.eventcenter.dto;

import java.util.List;

/**
 * 单个 Agent 的精简执行结果，供桌面端解释本次命令经过了哪些步骤。
 */
public record WorkspaceCommandAgentResponse(
        String agent,
        String status,
        String replyDraft,
        List<String> nextActions
) {
}
