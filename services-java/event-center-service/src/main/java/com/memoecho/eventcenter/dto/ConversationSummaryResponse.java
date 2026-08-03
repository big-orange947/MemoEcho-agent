package com.memoecho.eventcenter.dto;

import java.util.List;

public record ConversationSummaryResponse(
        String platform,
        String chatType,
        String chatId,
        String chatName,
        String lastSenderName,
        String lastMessage,
        String lastMessageTime,
        String lastRoute,
        String lastDispatchMode,
        String lastProcessingStatus,
        String lastWriteBackStatus,
        boolean actionRequired,
        Integer unreadLikeCount,
        Integer urgentCount,
        boolean autoReplyEnabled,
        boolean summaryEnabled,
        List<String> aliases
) {
    /**
     * 兼容尚未显式提供联系人别名的旧调用点，避免本次数据契约升级影响现有摘要接口。
     */
    public ConversationSummaryResponse(
            String platform,
            String chatType,
            String chatId,
            String chatName,
            String lastSenderName,
            String lastMessage,
            String lastMessageTime,
            String lastRoute,
            String lastDispatchMode,
            String lastProcessingStatus,
            String lastWriteBackStatus,
            boolean actionRequired,
            Integer unreadLikeCount,
            Integer urgentCount,
            boolean autoReplyEnabled,
            boolean summaryEnabled
    ) {
        this(
                platform, chatType, chatId, chatName, lastSenderName, lastMessage, lastMessageTime,
                lastRoute, lastDispatchMode, lastProcessingStatus, lastWriteBackStatus, actionRequired,
                unreadLikeCount, urgentCount, autoReplyEnabled, summaryEnabled, List.of()
        );
    }

    /** 保证序列化给 Agent Runtime 的别名集合始终非空且不可变。 */
    public ConversationSummaryResponse {
        aliases = aliases == null ? List.of() : List.copyOf(aliases);
    }
}
