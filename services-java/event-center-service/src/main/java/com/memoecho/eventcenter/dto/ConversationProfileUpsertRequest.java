package com.memoecho.eventcenter.dto;

import com.memoecho.eventcenter.model.ConversationProfileContext;
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
        Boolean includeUrgentInDigest,
        Integer maxReplyChars,
        Boolean splitLongReply,
        Integer splitReplyChancePercent,
        Boolean privateHistoryEnabled,
        Integer historyMaxMessages,
        Integer historyMaxChars,
        Boolean historyTrainingEnabled,
        String reviewMode,
        List<String> knowledgeBaseSources,
        ConversationProfileContext profileContext
) {
    /** 兼容 2.0 上线前的完整请求构造调用。 */
    public ConversationProfileUpsertRequest(
            String name, String description, Boolean enabled, String platform, String accountId, String scene,
            String chatType, List<String> chatIds, List<String> targetUserIds, List<String> supportedRoutes,
            String triggerMode, List<String> triggerKeywords, String personaMode, String systemPrompt,
            String skillReference, List<String> skillReferences, String modelProfileId, String preferredRoute,
            String replyMode, Integer replyDelaySecondsMin, Integer replyDelaySecondsMax, List<String> allowedTools,
            Boolean requireHumanConfirmation, Integer priority, String notificationMode,
            List<String> notificationKeywords, Integer digestWindowSeconds, Integer digestMaxMessages,
            Boolean includeUrgentInDigest, Integer maxReplyChars, Boolean splitLongReply,
            Integer splitReplyChancePercent, Boolean privateHistoryEnabled, Integer historyMaxMessages,
            Integer historyMaxChars, Boolean historyTrainingEnabled, String reviewMode,
            List<String> knowledgeBaseSources
    ) {
        this(name, description, enabled, platform, accountId, scene, chatType, chatIds, targetUserIds,
                supportedRoutes, triggerMode, triggerKeywords, personaMode, systemPrompt, skillReference,
                skillReferences, modelProfileId, preferredRoute, replyMode, replyDelaySecondsMin,
                replyDelaySecondsMax, allowedTools, requireHumanConfirmation, priority, notificationMode,
                notificationKeywords, digestWindowSeconds, digestMaxMessages, includeUrgentInDigest,
                maxReplyChars, splitLongReply, splitReplyChancePercent, privateHistoryEnabled,
                historyMaxMessages, historyMaxChars, historyTrainingEnabled, reviewMode,
                knowledgeBaseSources, ConversationProfileContext.empty());
    }
    /**
     * 兼容旧客户端提交的完整通知策略字段；回复形态配置使用默认值。
     */
    public ConversationProfileUpsertRequest(
            String name, String description, Boolean enabled, String platform, String accountId, String scene,
            String chatType, List<String> chatIds, List<String> targetUserIds, List<String> supportedRoutes,
            String triggerMode, List<String> triggerKeywords, String personaMode, String systemPrompt,
            String skillReference, List<String> skillReferences, String modelProfileId, String preferredRoute,
            String replyMode, Integer replyDelaySecondsMin, Integer replyDelaySecondsMax, List<String> allowedTools,
            Boolean requireHumanConfirmation, Integer priority, String notificationMode,
            List<String> notificationKeywords, Integer digestWindowSeconds, Integer digestMaxMessages,
            Boolean includeUrgentInDigest
    ) {
        this(name, description, enabled, platform, accountId, scene, chatType, chatIds, targetUserIds,
                supportedRoutes, triggerMode, triggerKeywords, personaMode, systemPrompt, skillReference,
                skillReferences, modelProfileId, preferredRoute, replyMode, replyDelaySecondsMin,
                replyDelaySecondsMax, allowedTools, requireHumanConfirmation, priority, notificationMode,
                notificationKeywords, digestWindowSeconds, digestMaxMessages, includeUrgentInDigest,
                24, true, 33, false, 12, 2000, false, "STRICT_HANDOFF", List.of(),
                ConversationProfileContext.empty());
    }

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
                "AUTO", List.of(), null, null, false, 24, true, 33, false, 12, 2000, false,
                "STRICT_HANDOFF", List.of(), ConversationProfileContext.empty());
    }
}
