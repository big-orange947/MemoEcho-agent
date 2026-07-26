package com.memoecho.eventcenter.dto;

/** Runtime 每处理完一个事件后提交的幂等任务状态，不包含工具令牌或模型密钥。 */
public record DelegatedTaskRuntimeUpdateRequest(
        String status,
        String progressSummary,
        String stateJson,
        String lastEventId,
        String completionReport
) {
}
