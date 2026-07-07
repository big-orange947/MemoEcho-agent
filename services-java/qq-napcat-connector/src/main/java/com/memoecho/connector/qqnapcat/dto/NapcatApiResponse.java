package com.memoecho.connector.qqnapcat.dto;

public record NapcatApiResponse<T>(
        String status,
        Integer retcode,
        T data,
        String message,
        String wording,
        String echo
) {
}
