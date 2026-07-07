package com.memoecho.connector.qqnapcat.dto;

import jakarta.validation.constraints.NotBlank;

import java.util.LinkedHashMap;
import java.util.Map;

public record MessageSegmentPayload(
        @NotBlank String type,
        Map<String, Object> data
) {

    public Map<String, Object> toMap() {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("type", type);
        payload.put("data", data != null ? data : Map.of());
        return payload;
    }
}
