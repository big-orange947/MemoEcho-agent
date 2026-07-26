package com.memoecho.eventcenter.dto;

import java.util.List;

/**
 * QCE 导入预览结果，供客户端在真正写库前展示会话和媒体统计。
 */
public record QceImportPreviewResponse(
        String chatName,
        String detectedChatType,
        String detectedChatId,
        String selfId,
        boolean requiresChatIdMapping,
        int totalMessages,
        int textMessages,
        int attachmentMessages,
        int imageAttachments,
        int videoAttachments,
        int audioAttachments,
        int fileAttachments,
        String startedAt,
        String endedAt,
        List<QceImportMessagePreview> samples,
        List<String> warnings
) {
}
