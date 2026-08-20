package com.memoecho.eventcenter.dto;

import jakarta.validation.constraints.NotBlank;

import java.time.Instant;
import java.util.Map;

/**
 * Runtime 在每次 LangGraph 执行前提交的当前入站事件（L0）。
 *
 * <p>会话范围不从这里推导：Java 使用步骤自身固化的 conversationScope 写入，
 * 确保历史查询与 L0 都属于任务绑定的会话，而不是 Runtime 临时从当前事件猜测。
 */
public record DelegatedTaskCurrentEventUpsertRequest(
        @NotBlank String eventId,
        String eventType,
        String senderId,
        String text,
        Instant occurredAt,
        Map<String, Object> payload
) {
}
