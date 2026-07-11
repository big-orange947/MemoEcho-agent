package com.memoecho.eventcenter.dto;

import java.time.Instant;
import java.util.List;

public record ConversationProfileResponse(
        String id,
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
     * 兼容旧版响应构造代码，默认返回自动通知策略。
     */
    public ConversationProfileResponse(
            String id, String name, String description, boolean enabled, String platform, String accountId,
            String scene, String chatType, List<String> chatIds, List<String> targetUserIds,
            List<String> supportedRoutes, String triggerMode, List<String> triggerKeywords, String personaMode,
            String systemPrompt, String skillReference, List<String> skillReferences, String modelProfileId,
            String preferredRoute, String replyMode, Integer replyDelaySecondsMin, Integer replyDelaySecondsMax,
            List<String> allowedTools, boolean requireHumanConfirmation, int priority, Instant createdAt, Instant updatedAt
    ) {
        this(id, name, description, enabled, platform, accountId, scene, chatType, chatIds, targetUserIds,
                supportedRoutes, triggerMode, triggerKeywords, personaMode, systemPrompt, skillReference,
                skillReferences, modelProfileId, preferredRoute, replyMode, replyDelaySecondsMin,
                replyDelaySecondsMax, allowedTools, requireHumanConfirmation, priority, createdAt, updatedAt,
                "AUTO", List.of(), null, null, false);
    }
}
