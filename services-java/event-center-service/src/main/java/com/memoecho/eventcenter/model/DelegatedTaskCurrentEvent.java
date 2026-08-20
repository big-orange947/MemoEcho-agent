package com.memoecho.eventcenter.model;

import java.time.Instant;

/**
 * 委托步骤最近一次处理的入站事件（L0 当前事件）。
 *
 * <p>每次 LangGraph 执行前由 Runtime 写入，作为当前事件不可丢失的持久化事实源。
 * 即使历史消息查询接口失败，Runtime 仍可基于该事件继续推理；
 * 服务重启后也可以从这里恢复“最后处理到哪一条事件”。
 */
public record DelegatedTaskCurrentEvent(
        String taskId,
        String workflowId,
        String stepKey,
        String conversationScopeJson,
        String eventId,
        String eventType,
        String senderId,
        String text,
        Instant occurredAt,
        String payloadJson,
        Instant updatedAt
) {
}
