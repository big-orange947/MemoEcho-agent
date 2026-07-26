package com.memoecho.eventcenter.model;

import java.time.Instant;
import java.util.List;

/**
 * 一张可追溯的会话认知卡。
 *
 * <p>认知卡保存“当前怎样理解这段关系和对话”，它会随着历史消息变化；Conversation Profile
 * 保存“用户希望 Agent 怎样做”，两者不能混为一套事实。每个推断字段都携带来源、置信度和锁定状态，
 * 用户确认或覆盖后的内容不会被下一次模型分析静默改写。</p>
 */
public record ConversationCognitionCard(
        String id,
        String userId,
        String platform,
        String chatType,
        String chatId,
        int version,
        CognitionField relationship,
        CognitionField preferredAddress,
        CognitionField counterpartyTraits,
        CognitionField ownerExpressionHabits,
        CognitionField counterpartyExpressionHabits,
        CognitionField backgroundSummary,
        CognitionField currentProgress,
        List<String> knownFacts,
        List<String> recentTopics,
        List<String> openQuestions,
        List<String> sourceEventIds,
        int sourceMessageCount,
        String status,
        Instant analyzedAt,
        Instant createdAt,
        Instant updatedAt
) {

    /**
     * 一项带证据等级的会话认知。
     *
     * <p>source 只允许 AI_INFERRED、USER_CONFIRMED、USER_OVERRIDE 或 GLOBAL_DEFAULT。
     * locked=true 表示模型刷新必须保留该值。</p>
     */
    public record CognitionField(String value, String source, double confidence, boolean locked) {
        /** 创建没有结论的字段，避免接口和 Prompt 出现 null 分支。 */
        public static CognitionField empty() {
            return new CognitionField("", "AI_INFERRED", 0.0d, false);
        }
    }
}
