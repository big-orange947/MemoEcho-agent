package com.memoecho.eventcenter.dto;

/** 事件租约的申请结果；只有 claimed 为 true 时 Runtime 才能继续执行。 */
public record DelegatedTaskEventClaimResponse(boolean claimed, String claimToken, String status) {
}
