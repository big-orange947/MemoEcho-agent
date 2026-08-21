package com.memoecho.eventcenter.dto;

/** 主控台对话线程中的一条历史消息，供多轮追问解析前文。 */
public record WorkspaceThreadHistoryEntry(
        String role,
        String content
) {
}