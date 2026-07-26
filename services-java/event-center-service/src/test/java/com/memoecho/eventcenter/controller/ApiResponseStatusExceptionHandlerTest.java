package com.memoecho.eventcenter.controller;

import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.server.ResponseStatusException;

import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;

class ApiResponseStatusExceptionHandlerTest {

    @Test
    void shouldExposeSafeBusinessReasonToClient() {
        // 验证下载失败时前端能够看到具体原因，而不是只看到 HTTP 状态名称。
        ApiResponseStatusExceptionHandler handler = new ApiResponseStatusExceptionHandler();

        ResponseEntity<Map<String, Object>> response = handler.handleResponseStatusException(
                new ResponseStatusException(HttpStatus.BAD_GATEWAY, "GitHub SKILL.md 下载失败，HTTP 502")
        );

        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.BAD_GATEWAY);
        assertThat(response.getBody()).containsEntry("message", "GitHub SKILL.md 下载失败，HTTP 502");
    }
}
