package com.memoecho.connector.qqnapcat.dto;

import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Positive;
import jakarta.validation.constraints.Size;

/**
 * Memo Echo 内部群管理请求。
 *
 * <p>这里故意不接收原始 NapCat action 和任意参数 Map，避免上层绕过权限策略调用
 * 未审核的 OneBot 接口。新增能力时必须同时修改本 DTO 和 NapcatGroupService 白名单。</p>
 */
public record GroupOperationRequest(
        @NotBlank String action,
        @NotNull @Positive Long groupId,
        @Positive Long targetUserId,
        @Min(0) @Max(2_592_000) Integer durationSeconds,
        @Size(max = 3000) String text,
        Boolean enable,
        @Positive Long messageId,
        Boolean rejectAddRequest
) {
}
