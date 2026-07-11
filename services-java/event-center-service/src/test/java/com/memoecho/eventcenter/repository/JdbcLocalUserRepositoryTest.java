package com.memoecho.eventcenter.repository;

import com.memoecho.eventcenter.model.LocalUser;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.jdbc.JdbcTest;
import org.springframework.context.annotation.Import;
import org.springframework.test.context.TestPropertySource;

import java.time.Instant;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

@JdbcTest
@Import(JdbcLocalUserRepository.class)
@TestPropertySource(properties = "spring.sql.init.mode=always")
class JdbcLocalUserRepositoryTest {

    @Autowired
    private JdbcLocalUserRepository repository;

    @Test
    void shouldPersistAndFindLocalUserByUsername() {
        // 这个测试函数的作用是验证本地用户和密码哈希能够正确落库并按用户名读取。
        Instant now = Instant.parse("2026-07-10T10:00:00Z");
        repository.save(new LocalUser("user-001", "freeze", "Freeze", "pbkdf2-hash", true, now, now));

        LocalUser loaded = repository.findByUsername("freeze").orElseThrow();

        assertEquals("user-001", loaded.id());
        assertEquals("pbkdf2-hash", loaded.passwordHash());
        assertTrue(repository.findById("user-001").isPresent());
    }
}
