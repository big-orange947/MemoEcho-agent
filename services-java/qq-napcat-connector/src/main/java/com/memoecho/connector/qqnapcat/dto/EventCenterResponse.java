package com.memoecho.connector.qqnapcat.dto;

import com.fasterxml.jackson.databind.JsonNode;

public record EventCenterResponse(
        boolean forwarded,
        Integer httpStatus,
        JsonNode body,
        String error
) {
}
