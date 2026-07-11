package com.memoecho.eventcenter.model;

import java.time.Instant;
import java.util.List;

public record ConversationProfile(
        String id,
        String userId,
        String name,
        String description,
        boolean enabled,
        String platform,
        String accountId,
        String scene,
        String chatType,
        List<String> chatIds,
        List<String> targetUserIds,
        List<String> supportedRoutes,
        String triggerMode,
        List<String> triggerKeywords,
        String personaMode,
        String systemPrompt,
        String skillReference,
        List<String> skillReferences,
        String modelProfileId,
        String preferredRoute,
        String replyMode,
        Integer replyDelaySecondsMin,
        Integer replyDelaySecondsMax,
        List<String> allowedTools,
        boolean requireHumanConfirmation,
        int priority,
        Instant createdAt,
        Instant updatedAt,
        String notificationMode,
        List<String> notificationKeywords,
        Integer digestWindowSeconds,
        Integer digestMaxMessages,
        boolean includeUrgentInDigest
) {
    /**
     * 兼容旧版未携带用户归属的完整构造调用，旧数据统一归入 default 用户。
     */
    public ConversationProfile(
            String id, String name, String description, boolean enabled, String platform, String accountId,
            String scene, String chatType, List<String> chatIds, List<String> targetUserIds,
            List<String> supportedRoutes, String triggerMode, List<String> triggerKeywords, String personaMode,
            String systemPrompt, String skillReference, List<String> skillReferences, String modelProfileId,
            String preferredRoute, String replyMode, Integer replyDelaySecondsMin, Integer replyDelaySecondsMax,
            List<String> allowedTools, boolean requireHumanConfirmation, int priority, Instant createdAt, Instant updatedAt,
            String notificationMode, List<String> notificationKeywords, Integer digestWindowSeconds,
            Integer digestMaxMessages, boolean includeUrgentInDigest
    ) {
        this(id, "default", name, description, enabled, platform, accountId, scene, chatType, chatIds,
                targetUserIds, supportedRoutes, triggerMode, triggerKeywords, personaMode, systemPrompt,
                skillReference, skillReferences, modelProfileId, preferredRoute, replyMode, replyDelaySecondsMin,
                replyDelaySecondsMax, allowedTools, requireHumanConfirmation, priority, createdAt, updatedAt,
                notificationMode, notificationKeywords, digestWindowSeconds, digestMaxMessages, includeUrgentInDigest);
    }

    /**
     * 兼容尚未配置通知策略的旧设定集，默认沿用自动双通道行为。
     */
    public ConversationProfile(
            String id, String name, String description, boolean enabled, String platform, String accountId,
            String scene, String chatType, List<String> chatIds, List<String> targetUserIds,
            List<String> supportedRoutes, String triggerMode, List<String> triggerKeywords, String personaMode,
            String systemPrompt, String skillReference, List<String> skillReferences, String modelProfileId,
            String preferredRoute, String replyMode, Integer replyDelaySecondsMin, Integer replyDelaySecondsMax,
            List<String> allowedTools, boolean requireHumanConfirmation, int priority, Instant createdAt, Instant updatedAt
    ) {
        this(id, "default", name, description, enabled, platform, accountId, scene, chatType, chatIds, targetUserIds,
                supportedRoutes, triggerMode, triggerKeywords, personaMode, systemPrompt, skillReference,
                skillReferences, modelProfileId, preferredRoute, replyMode, replyDelaySecondsMin,
                replyDelaySecondsMax, allowedTools, requireHumanConfirmation, priority, createdAt, updatedAt,
                "AUTO", List.of(), null, null, false);
    }
}
