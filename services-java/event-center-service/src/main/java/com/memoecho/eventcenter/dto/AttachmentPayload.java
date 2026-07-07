package com.memoecho.eventcenter.dto;

public record AttachmentPayload(
        String fileId,
        String fileName,
        String fileType,
        String url
) {
}
