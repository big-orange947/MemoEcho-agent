package com.memoecho.eventcenter.repository;

import com.memoecho.eventcenter.model.MemoryCandidate;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.jdbc.JdbcTest;
import org.springframework.context.annotation.Import;
import org.springframework.test.context.TestPropertySource;

import java.time.Instant;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** 验证长期记忆仓储的用户隔离、确认过滤和自动过期行为。 */
@JdbcTest
@Import(JdbcMemoryCandidateRepository.class)
@TestPropertySource(properties = "spring.sql.init.mode=always")
class JdbcMemoryCandidateRepositoryTest {

    @Autowired
    private JdbcMemoryCandidateRepository repository;

    /** 相同 ID 也不能由其他用户读取。 */
    @Test
    void shouldEnforceOwnershipBoundary() {
        repository.save(candidate("memory-1", "freeze", "CANDIDATE", null));

        assertTrue(repository.findByIdAndUserId("memory-1", "freeze").isPresent());
        assertTrue(repository.findByIdAndUserId("memory-1", "another-user").isEmpty());
    }

    /** Runtime 查询只能得到已确认且尚未过期的记忆。 */
    @Test
    void shouldReturnOnlyActiveVerifiedMemories() {
        Instant now = Instant.parse("2026-07-17T01:00:00Z");
        repository.save(candidate("verified", "freeze", "VERIFIED", now.plusSeconds(60)));
        repository.save(candidate("candidate", "freeze", "CANDIDATE", now.plusSeconds(60)));
        repository.save(candidate("expired", "freeze", "VERIFIED", now.minusSeconds(1)));

        assertEquals(1, repository.findVerifiedByUserId("freeze", now).size());
        assertEquals("verified", repository.findVerifiedByUserId("freeze", now).getFirst().id());
        assertEquals(1, repository.expireDueMemories("freeze", now));
        assertEquals("EXPIRED", repository.findByIdAndUserId("expired", "freeze").orElseThrow().status());
    }

    /** 构造一条带可信 OWNER 来源的测试候选。 */
    private MemoryCandidate candidate(String id, String userId, String status, Instant expiresAt) {
        Instant now = Instant.parse("2026-07-17T00:00:00Z");
        return new MemoryCandidate(
                id, userId, "freeze", "常用称呼", "橙子", "GLOBAL", "", "", "", "",
                "[\"event-1\"]", "OWNER", "human_self", 0.95d, status, "",
                now, now, expiresAt, now, now
        );
    }
}
