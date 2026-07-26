package com.memoecho.eventcenter.dto;

import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;

/**
 * Python Runtime 提交已经通过 LangGraph 编译的委托任务。
 * Java 只负责用户归属、联系人白名单和持久化校验，不再重复做自然语言理解。
 */
public record DelegatedTaskRuntimeCreateRequest(
        @NotBlank String command,
        @NotNull @Valid DelegatedTaskCompilationResponse compilation
) {
}
