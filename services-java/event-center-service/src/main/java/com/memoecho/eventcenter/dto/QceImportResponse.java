package com.memoecho.eventcenter.dto;

/**
 * QCE 历史消息导入的最终统计。
 */
public record QceImportResponse(
        String chatId,
        String chatType,
        int importedCount,
        int duplicateCount,
        int attachmentCount,
        String message
) {
}
