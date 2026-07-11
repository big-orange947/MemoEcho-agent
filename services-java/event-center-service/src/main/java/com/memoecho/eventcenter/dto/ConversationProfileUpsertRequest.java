package com.memoecho.eventcenter.dto;

import jakarta.validation.constraints.NotBlank;

import java.util.List;

public record ConversationProfileUpsertRequest(
        @NotBlank String name,
        String description,
        Boolean enabled,
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
        Boolean requireHumanConfirmation,
        Integer priority,
        String notificationMode,
        List<String> notificationKeywords,
        Integer digestWindowSeconds,
        Integer digestMaxMessages,
        Boolean includeUrgentInDigest
) {
    /**
     * 兼容前端尚未传递通知策略字段的旧请求。
     */
    public ConversationProfileUpsertRequest(
            String name, String description, Boolean enabled, String platform, String accountId, String scene,
            String chatType, List<String> chatIds, List<String> targetUserIds, List<String> supportedRoutes,
            String triggerMode, List<String> triggerKeywords, String personaMode, String systemPrompt,
            String skillReference, List<String> skillReferences, String modelProfileId, String preferredRoute,
            String replyMode, Integer replyDelaySecondsMin, Integer replyDelaySecondsMax, List<String> allowedTools,
            Boolean requireHumanConfirmation, Integer priority
    ) {
        this(name, description, enabled, platform, accountId, scene, chatType, chatIds, targetUserIds,
                supportedRoutes, triggerMode, triggerKeywords, personaMode, systemPrompt, skillReference,
                skillReferences, modelProfileId, preferredRoute, replyMode, replyDelaySecondsMin,
                replyDelaySecondsMax, allowedTools, requireHumanConfirmation, priority,
                "AUTO", List.of(), null, null, false);
    }
}
