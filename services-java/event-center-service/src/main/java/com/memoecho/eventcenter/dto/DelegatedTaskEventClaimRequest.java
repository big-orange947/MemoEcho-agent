package com.memoecho.eventcenter.dto;

/** Runtime 申请处理某条委托任务事件的短期租约。 */
public record DelegatedTaskEventClaimRequest(String eventId, Integer leaseSeconds) {
}
