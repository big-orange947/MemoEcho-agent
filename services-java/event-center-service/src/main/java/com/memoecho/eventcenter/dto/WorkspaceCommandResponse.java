package com.memoecho.eventcenter.dto;

import java.util.List;

/**
 * 桌面命令的稳定响应，不把 Python Runtime 的内部 JSON 结构直接暴露给客户端。
 */
public record WorkspaceCommandResponse(
        String commandId,
        String status,
        String route,
        String summary,
        String finalReply,
        boolean needConfirmation,
        List<WorkspaceCommandAgentResponse> results,
        DelegatedTaskResponse delegatedTask,
        String error
) {
}
