package com.memoecho.eventcenter.dto;

/** 会话历史接口中返回的附件异步分析结果。 */
public record MediaAnalysisResponse(
        String attachmentId,
        String fileName,
        String fileType,
        String status,
        String summary,
        String extractedText
) {
}
