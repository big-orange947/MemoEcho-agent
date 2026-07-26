package com.memoecho.eventcenter.dto;

import jakarta.validation.Valid;
import jakarta.validation.constraints.NotEmpty;

import java.util.List;

/** Runtime 异步附件任务回写事件分析结果的请求。 */
public record MediaAnalysisUpdateRequest(
        @NotEmpty List<@Valid MediaAnalysisItem> analyses
) {
}
