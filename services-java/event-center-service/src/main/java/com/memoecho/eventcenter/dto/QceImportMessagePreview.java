package com.memoecho.eventcenter.dto;

/**
 * 导入预览中的单条样本；仅展示必要字段，避免把整个导出文件回传给界面。
 */
public record QceImportMessagePreview(
        String messageId,
        String senderName,
        String text,
        String timestamp,
        int attachmentCount
) {
}
