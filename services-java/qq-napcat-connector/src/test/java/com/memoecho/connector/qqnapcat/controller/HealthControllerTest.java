package com.memoecho.connector.qqnapcat.controller;

import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

class HealthControllerTest {

    /**
     * 验证健康端点保持稳定返回 UP，避免一键启动脚本因返回协议变化而误判超时。
     */
    @Test
    void shouldReturnUpStatus() {
        assertThat(new HealthController().health()).containsEntry("status", "UP");
    }
}
