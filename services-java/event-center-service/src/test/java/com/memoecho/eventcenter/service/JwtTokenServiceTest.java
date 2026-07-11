package com.memoecho.eventcenter.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.memoecho.eventcenter.config.EventCenterSecurityProperties;
import com.memoecho.eventcenter.dto.AuthenticatedUser;
import com.memoecho.eventcenter.model.LocalUser;
import org.junit.jupiter.api.Test;

import java.time.Instant;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

class JwtTokenServiceTest {

    @Test
    void shouldIssueVerifyAndRejectTamperedToken() {
        // 这个测试函数的作用是验证 JWT 身份可恢复，且任意篡改都会导致签名校验失败。
        EventCenterSecurityProperties properties = new EventCenterSecurityProperties();
        properties.setJwtSecret("unit-test-jwt-secret");
        JwtTokenService service = new JwtTokenService(properties, new ObjectMapper());
        LocalUser user = new LocalUser(
                "user-001", "freeze", "Freeze", "hash", true, Instant.now(), Instant.now());

        String token = service.issue(user);
        AuthenticatedUser authenticated = service.verify(token);

        assertEquals("user-001", authenticated.userId());
        assertEquals("freeze", authenticated.username());
        assertThrows(IllegalArgumentException.class, () -> service.verify(token + "x"));
    }
}
