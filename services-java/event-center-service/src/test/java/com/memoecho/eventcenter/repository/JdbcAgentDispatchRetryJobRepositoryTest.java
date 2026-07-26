package com.memoecho.eventcenter.repository;

import com.memoecho.eventcenter.model.AgentDispatchRetryJob;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.jdbc.JdbcTest;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.TestPropertySource;

import java.time.Instant;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

@JdbcTest
@TestPropertySource(properties = "spring.sql.init.mode=always")
class JdbcAgentDispatchRetryJobRepositoryTest {

    @Autowired
    private JdbcTemplate jdbcTemplate;

    private JdbcAgentDispatchRetryJobRepository repository;

    @BeforeEach
    void setUp() {
        // 使用真实 H2 表验证持久化队列，避免只通过 Mockito 掩盖 SQL 兼容问题。
        repository = new JdbcAgentDispatchRetryJobRepository(jdbcTemplate);
    }

    @Test
    void shouldScheduleClaimAndFinishRetryJob() {
        // 验证任务从等待、原子领取到成功结束的完整数据库状态流转。
        Instant now = Instant.parse("2026-07-16T08:00:00Z");
        repository.schedule("event-jdbc-retry", 1, now.minusSeconds(1), "timeout", now);

        assertEquals(1, repository.findDue(now, 10).size());
        assertTrue(repository.claim("event-jdbc-retry", 1, now.plusSeconds(1)));
        assertFalse(repository.claim("event-jdbc-retry", 1, now.plusSeconds(2)));

        repository.markSucceeded("event-jdbc-retry", now.plusSeconds(3));

        AgentDispatchRetryJob completed = repository.findByEventId("event-jdbc-retry").orElseThrow();
        assertEquals("SUCCEEDED", completed.status());
        assertEquals(1, completed.attemptCount());
        assertTrue(repository.findDue(now.plusSeconds(10), 10).isEmpty());
    }

    @Test
    void shouldRescheduleExistingJobWithoutDuplicateRow() {
        // 验证多次临时失败只更新同一个 eventId，不会生成并行重复任务。
        Instant now = Instant.parse("2026-07-16T08:00:00Z");
        repository.schedule("event-jdbc-reschedule", 1, now, "first", now);
        repository.schedule("event-jdbc-reschedule", 2, now.plusSeconds(5), "second", now.plusSeconds(1));

        AgentDispatchRetryJob rescheduled = repository.findByEventId("event-jdbc-reschedule").orElseThrow();
        assertEquals("WAITING", rescheduled.status());
        assertEquals(2, rescheduled.attemptCount());
        assertEquals("second", rescheduled.lastError());
        assertEquals(1, jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM agent_dispatch_retry WHERE event_id = ?",
                Integer.class,
                "event-jdbc-reschedule"
        ));
    }
}
