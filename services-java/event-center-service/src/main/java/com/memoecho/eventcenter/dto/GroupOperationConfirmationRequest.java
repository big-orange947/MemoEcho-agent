package com.memoecho.eventcenter.dto;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

/**
 * 桌面端确认一次群管理动作时提交的最小请求。
 *
 * <p>请求中不包含 Runtime 的一次性令牌，避免高权限凭据进入浏览器状态或日志。</p>
 */
public record GroupOperationConfirmationRequest(
        @NotBlank @Size(max = 200) String confirmationText
) {
}
