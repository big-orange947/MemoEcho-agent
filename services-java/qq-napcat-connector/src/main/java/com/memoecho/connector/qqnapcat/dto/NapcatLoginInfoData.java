package com.memoecho.connector.qqnapcat.dto;

import com.fasterxml.jackson.annotation.JsonProperty;

public record NapcatLoginInfoData(
        @JsonProperty("user_id") Long userId,
        String nickname
) {
}
