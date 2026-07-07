package com.memoecho.connector.qqnapcat.dto;

import com.fasterxml.jackson.databind.JsonNode;

public record NapcatStatusData(
        boolean online,
        boolean good,
        JsonNode stat
) {
}
