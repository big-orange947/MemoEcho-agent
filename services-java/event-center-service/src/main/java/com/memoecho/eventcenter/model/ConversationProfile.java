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
        boolean includeUrgentInDigest,
        Integer maxReplyChars,
        Boolean splitLongReply,
        Integer splitReplyChancePercent,
        boolean privateHistoryEnabled,
        Integer historyMaxMessages,
        Integer historyMaxChars,
        boolean historyTrainingEnabled,
        String reviewMode,
        List<String> knowledgeBaseSources,
        ConversationProfileContext profileContext
) {
    /**
     * 返回只替换结构化上下文的新设定集实例。
     *
     * <p>record 本身不可变，该方法避免调用方为了更新 Profile 2.0 字段而重复整段构造参数。</p>
     */
    public ConversationProfile withProfileContext(ConversationProfileContext newProfileContext) {
        return new ConversationProfile(
                id, userId, name, description, enabled, platform, accountId, scene, chatType, chatIds,
                targetUserIds, supportedRoutes, triggerMode, triggerKeywords, personaMode, systemPrompt,
                skillReference, skillReferences, modelProfileId, preferredRoute, replyMode, replyDelaySecondsMin,
                replyDelaySecondsMax, allowedTools, requireHumanConfirmation, priority, createdAt, updatedAt,
                notificationMode, notificationKeywords, digestWindowSeconds, digestMaxMessages,
                includeUrgentInDigest, maxReplyChars, splitLongReply, splitReplyChancePercent,
                privateHistoryEnabled, historyMaxMessages, historyMaxChars, historyTrainingEnabled,
                reviewMode, knowledgeBaseSources,
                newProfileContext == null ? ConversationProfileContext.empty() : newProfileContext
        );
    }

    /**
     * 兼容 Conversation Profile 2.0 上线前的完整构造调用。
     * 旧数据自动补为空的结构化上下文，不改变原有人格提示词行为。
     */
    public ConversationProfile(
            String id, String userId, String name, String description, boolean enabled, String platform,
            String accountId, String scene, String chatType, List<String> chatIds, List<String> targetUserIds,
            List<String> supportedRoutes, String triggerMode, List<String> triggerKeywords, String personaMode,
            String systemPrompt, String skillReference, List<String> skillReferences, String modelProfileId,
            String preferredRoute, String replyMode, Integer replyDelaySecondsMin, Integer replyDelaySecondsMax,
            List<String> allowedTools, boolean requireHumanConfirmation, int priority, Instant createdAt,
            Instant updatedAt, String notificationMode, List<String> notificationKeywords,
            Integer digestWindowSeconds, Integer digestMaxMessages, boolean includeUrgentInDigest,
            Integer maxReplyChars, Boolean splitLongReply, Integer splitReplyChancePercent,
            boolean privateHistoryEnabled, Integer historyMaxMessages, Integer historyMaxChars,
            boolean historyTrainingEnabled, String reviewMode, List<String> knowledgeBaseSources
    ) {
        this(id, userId, name, description, enabled, platform, accountId, scene, chatType, chatIds,
                targetUserIds, supportedRoutes, triggerMode, triggerKeywords, personaMode, systemPrompt,
                skillReference, skillReferences, modelProfileId, preferredRoute, replyMode, replyDelaySecondsMin,
                replyDelaySecondsMax, allowedTools, requireHumanConfirmation, priority, createdAt, updatedAt,
                notificationMode, notificationKeywords, digestWindowSeconds, digestMaxMessages,
                includeUrgentInDigest, maxReplyChars, splitLongReply, splitReplyChancePercent,
                privateHistoryEnabled, historyMaxMessages, historyMaxChars, historyTrainingEnabled,
                reviewMode, knowledgeBaseSources, ConversationProfileContext.empty());
    }
    /**
     * 兼容已包含通知策略、但尚未包含回复形态配置的旧调用方。
     * 新字段使用保守默认值，避免旧设定在升级后意外变成长文本自动发送。
     */
    public ConversationProfile(
            String id, String userId, String name, String description, boolean enabled, String platform,
            String accountId, String scene, String chatType, List<String> chatIds, List<String> targetUserIds,
            List<String> supportedRoutes, String triggerMode, List<String> triggerKeywords, String personaMode,
            String systemPrompt, String skillReference, List<String> skillReferences, String modelProfileId,
            String preferredRoute, String replyMode, Integer replyDelaySecondsMin, Integer replyDelaySecondsMax,
            List<String> allowedTools, boolean requireHumanConfirmation, int priority, Instant createdAt, Instant updatedAt,
            String notificationMode, List<String> notificationKeywords, Integer digestWindowSeconds,
            Integer digestMaxMessages, boolean includeUrgentInDigest
    ) {
        this(id, userId, name, description, enabled, platform, accountId, scene, chatType, chatIds, targetUserIds,
                supportedRoutes, triggerMode, triggerKeywords, personaMode, systemPrompt, skillReference,
                skillReferences, modelProfileId, preferredRoute, replyMode, replyDelaySecondsMin,
                replyDelaySecondsMax, allowedTools, requireHumanConfirmation, priority, createdAt, updatedAt,
                notificationMode, notificationKeywords, digestWindowSeconds, digestMaxMessages, includeUrgentInDigest,
                24, true, 33, false, 12, 2000, false, "STRICT_HANDOFF", List.of(),
                ConversationProfileContext.empty());
    }

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
                notificationMode, notificationKeywords, digestWindowSeconds, digestMaxMessages, includeUrgentInDigest,
                24, true, 33, false, 12, 2000, false, "STRICT_HANDOFF", List.of(),
                ConversationProfileContext.empty());
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
                "AUTO", List.of(), null, null, false, 24, true, 33, false, 12, 2000, false,
                "STRICT_HANDOFF", List.of(), ConversationProfileContext.empty());
    }
}
