package com.memoecho.eventcenter.dto;

public record ConversationProfileMatchResponse(
        boolean matched,
        boolean active,
        String reason,
        ConversationProfileResponse profile,
        ConversationProxyTaskStateResponse taskState
) {
    /** 兼容没有任务生命周期的旧调用。 */
    public ConversationProfileMatchResponse(
            boolean matched, boolean active, String reason, ConversationProfileResponse profile
    ) {
        this(matched, active, reason, profile, null);
    }
}
