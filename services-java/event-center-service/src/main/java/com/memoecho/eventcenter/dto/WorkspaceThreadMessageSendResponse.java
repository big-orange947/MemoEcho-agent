package com.memoecho.eventcenter.dto;

/** 发送一条主控台消息的返回：用户消息、Agent 回执消息与命令执行响应。 */
public record WorkspaceThreadMessageSendResponse(
        WorkspaceThreadMessageResponse userMessage,
        WorkspaceThreadMessageResponse agentMessage,
        WorkspaceCommandResponse response
) {
}