package com.memoecho.eventcenter.dto;

import jakarta.validation.constraints.DecimalMax;
import jakarta.validation.constraints.DecimalMin;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

import java.time.Instant;
import java.util.List;

/** 创建或编辑长期记忆候选时使用的结构化请求，避免把整段聊天直接当作长期记忆。 */
public record MemoryCandidateUpsertRequest(
        @NotBlank @Size(max = 255) String subject,
        @NotBlank @Size(max = 128) String predicate,
        @NotBlank @Size(max = 10_000) String value,
        @Size(max = 32) String scopeType,
        @Size(max = 64) String platform,
        @Size(max = 64) String scene,
        @Size(max = 32) String chatType,
        @Size(max = 255) String chatId,
        @Size(max = 100) List<@Size(max = 255) String> sourceEventIds,
        @Size(max = 32) String sourceActorType,
        @Size(max = 32) String factAuthority,
        @DecimalMin("0.0") @DecimalMax("1.0") Double confidence,
        Instant expiresAt
) {
}
