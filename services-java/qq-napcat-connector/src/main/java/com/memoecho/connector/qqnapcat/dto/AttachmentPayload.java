package com.memoecho.connector.qqnapcat.dto;

import com.fasterxml.jackson.annotation.JsonInclude;

@JsonInclude(JsonInclude.Include.NON_NULL)
public record AttachmentPayload(
        String fileId,
        String fileName,
        String fileType,
        String url
) {
}

