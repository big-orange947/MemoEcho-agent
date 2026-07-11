package com.memoecho.eventcenter.repository;

import com.memoecho.eventcenter.model.PlatformConnection;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.jdbc.JdbcTest;
import org.springframework.context.annotation.Import;
import org.springframework.test.context.TestPropertySource;

import java.time.Instant;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

@JdbcTest
@Import(JdbcPlatformConnectionRepository.class)
@TestPropertySource(properties = "spring.sql.init.mode=always")
class JdbcPlatformConnectionRepositoryTest {

    @Autowired
    private JdbcPlatformConnectionRepository repository;

    @Test
    void shouldPersistEncryptedConnectionAndEnforceUserOwnershipInQueries() {
        // 这个测试函数的作用是验证连接密文可以持久化，并且同一 ID 不能被其他用户查询到。
        Instant now = Instant.parse("2026-07-10T10:00:00Z");
        repository.save(new PlatformConnection(
                "connection-001", "user-001", "我的 QQ", "qq", "napcat", true,
                "http://127.0.0.1:8091", "enc::ciphertext", "3969785168", "哈吉仙",
                "HEALTHY", "NapCat 已连接。", now, now, now));

        PlatformConnection loaded = repository.findByIdAndUserId("connection-001", "user-001").orElseThrow();
        assertEquals("enc::ciphertext", loaded.credentialCiphertext());
        assertEquals("3969785168", loaded.accountId());
        assertTrue(repository.findByIdAndUserId("connection-001", "user-002").isEmpty());
    }
}
