package com.memoecho.eventcenter.service;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

class PasswordHashServiceTest {

    @Test
    void shouldHashPasswordWithRandomSaltAndVerifyIt() {
        // 这个测试函数的作用是验证相同密码产生不同哈希，同时仍能正确校验。
        PasswordHashService service = new PasswordHashService();

        String first = service.hash("safe-password-123");
        String second = service.hash("safe-password-123");

        assertNotEquals(first, second);
        assertTrue(service.matches("safe-password-123", first));
        assertFalse(service.matches("wrong-password", first));
    }
}
