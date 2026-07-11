package com.memoecho.eventcenter.service;

import com.memoecho.eventcenter.config.EventCenterSecurityProperties;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

class ApiKeyCryptoServiceTest {

    @Test
    void shouldEncryptAndDecryptApiKey() {
        // 这个测试函数的作用是验证 API Key 可以被正确加密，并能无损解密回来。
        EventCenterSecurityProperties properties = new EventCenterSecurityProperties();
        properties.setApiKeySecret("unit-test-secret");
        ApiKeyCryptoService cryptoService = new ApiKeyCryptoService(properties);

        String encrypted = cryptoService.encrypt("sk-demo-001");
        String decrypted = cryptoService.decrypt(encrypted);

        assertNotEquals("sk-demo-001", encrypted);
        assertTrue(cryptoService.isEncrypted(encrypted));
        assertEquals("sk-demo-001", decrypted);
    }

    @Test
    void shouldKeepPlaintextCompatibleForMigration() {
        // 这个测试函数的作用是验证历史明文数据在切换到加密后仍然可以被正常读取。
        EventCenterSecurityProperties properties = new EventCenterSecurityProperties();
        properties.setApiKeySecret("unit-test-secret");
        ApiKeyCryptoService cryptoService = new ApiKeyCryptoService(properties);

        assertEquals("sk-legacy-plain", cryptoService.decrypt("sk-legacy-plain"));
    }
}
