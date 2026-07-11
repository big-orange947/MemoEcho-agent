package com.memoecho.eventcenter.dto;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

/**
 * 桌面客户端提交给 Agent 的自然语言命令。
 */
public record WorkspaceCommandRequest(
        @NotBlank @Size(max = 8000) String prompt,
        @Size(max = 64) String requestedRoute
) {
}
