package com.memoecho.eventcenter.dto;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

import java.util.List;

/**
 * 桌面客户端提交给 Agent 的自然语言命令。
 *
 * threadHistory 由主控台对话线程在发送前组装（最近若干条 user/agent 消息），
 * 随命令事件透传给 Python Runtime，用于多轮追问（如"那后天呢？"）解析前文。
 */
public record WorkspaceCommandRequest(
        @NotBlank @Size(max = 8000) String prompt,
        @Size(max = 64) String requestedRoute,
        List<WorkspaceThreadHistoryEntry> threadHistory
) {
    /** 兼容旧调用：无线程历史。 */
    public WorkspaceCommandRequest(String prompt, String requestedRoute) {
        this(prompt, requestedRoute, null);
    }
}