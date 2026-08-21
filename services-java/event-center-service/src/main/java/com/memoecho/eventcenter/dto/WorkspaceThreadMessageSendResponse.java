package com.memoecho.eventcenter.dto;

/** 发送一条主控台消息的返回：用户消息、streaming agent 消息与预生成的命令 executionId。 */
public record WorkspaceThreadMessageSendResponse(
        WorkspaceThreadMessageResponse userMessage,
        WorkspaceThreadMessageResponse agentMessage,
        String commandId
) {
}