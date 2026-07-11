package com.memoecho.eventcenter.dto;

public record UserModelProfileResolveResponse(
        boolean matched,
        String reason,
        UserModelProfileResolvedResponse profile
) {
}
