package com.memoecho.eventcenter.dto;

import java.util.List;

/**
 * 长期记忆候选的来源证据视图。
 *
 * @param candidateId 候选记忆 ID
 * @param sourceEventIds 候选声明的全部来源事件 ID
 * @param messages 围绕来源事件合并、去重并按时间排序后的聊天上下文
 * @param missingEventIds 已不存在或不属于当前用户的来源事件 ID
 */
public record MemoryCandidateEvidenceResponse(
        String candidateId,
        List<String> sourceEventIds,
        List<ConversationMessageResponse> messages,
        List<String> missingEventIds
) {
}
