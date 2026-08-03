package com.memoecho.eventcenter.dto;

/** Runtime 完成事件处理时提交的租约凭证。 */
public record DelegatedTaskEventCompleteRequest(String eventId, String claimToken) {
}
