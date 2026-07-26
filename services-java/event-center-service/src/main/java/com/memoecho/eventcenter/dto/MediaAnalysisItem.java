package com.memoecho.eventcenter.dto;

/**
 * Runtime 对单个附件完成异步解析后回写的安全结果。
 * 不保存原始二进制内容，只保存后续上下文和工作台能够使用的简短文本。
 */
public record MediaAnalysisItem(
        String attachmentId,
        String fileName,
        String fileType,
        String status,
        String summary,
        String extractedText
) {
}
