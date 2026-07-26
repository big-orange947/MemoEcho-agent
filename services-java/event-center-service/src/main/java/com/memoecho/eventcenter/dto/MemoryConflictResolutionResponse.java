package com.memoecho.eventcenter.dto;

import java.util.List;

/**
 * 冲突处理结果。
 *
 * @param candidate 处理后的候选记录
 * @param supersededMemoryIds 采用候选值时被替代的旧记忆 ID
 */
public record MemoryConflictResolutionResponse(
        MemoryCandidateResponse candidate,
        List<String> supersededMemoryIds
) {
}
