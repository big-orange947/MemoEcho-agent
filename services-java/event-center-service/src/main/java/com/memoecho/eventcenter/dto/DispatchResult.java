package com.memoecho.eventcenter.dto;

import com.fasterxml.jackson.databind.JsonNode;

public record DispatchResult(
        boolean attempted,
        Integer httpStatus,
        JsonNode body,
        String error
) {
}
