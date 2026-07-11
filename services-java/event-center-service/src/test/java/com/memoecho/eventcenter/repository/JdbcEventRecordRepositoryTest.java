package com.memoecho.eventcenter.repository;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.memoecho.eventcenter.dto.SenderPayload;
import com.memoecho.eventcenter.dto.UnifiedEventPayload;
import com.memoecho.eventcenter.model.AgentExecutionStep;
import com.memoecho.eventcenter.model.ExecutionTrace;
import com.memoecho.eventcenter.model.StoredEvent;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.jdbc.JdbcTest;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.TestPropertySource;

import java.time.Instant;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

@JdbcTest
@TestPropertySource(properties = {
        "spring.sql.init.mode=always"
})
class JdbcEventRecordRepositoryTest {

    @Autowired
    private JdbcTemplate jdbcTemplate;

    private final ObjectMapper objectMapper = new ObjectMapper();
    private JdbcEventRecordRepository repository;

    @BeforeEach
    void setUp() {
        // 这个测试准备函数的作用是使用嵌入式 H2 和真实 JdbcTemplate 构造事件数据库仓储。
        repository = new JdbcEventRecordRepository(jdbcTemplate, objectMapper);
    }

    @Test
    void shouldPersistAndUpdateEventStateIncludingTrace() {
        // 这个测试函数的作用是验证事件、草稿、收件箱状态和脱敏执行轨迹可以跨数据库读写完整保留。
        UnifiedEventPayload payload = new UnifiedEventPayload(
                "qq:message:private:persistence-1",
                "qq",
                "life",
                "message",
                "private",
                "2597164807",
                "3969785168",
                new SenderPayload("2597164807", "freeze", "member"),
                "下午两点开会",
                List.of(),
                List.of("3969785168"),
                "2026-07-10T08:00:00Z",
                objectMapper.createObjectNode().put("source", "jdbc-test")
        );
        Instant receivedAt = Instant.parse("2026-07-10T08:00:01Z");
        ExecutionTrace trace = new ExecutionTrace(
                "execution-jdbc-1",
                "social_reply",
                "草稿生成完成",
                List.of("qq_write_back_skipped"),
                List.of(new AgentExecutionStep(
                        "social",
                        "success",
                        List.of("send_qq_message"),
                        List.of("await_user_confirmation"),
                        true
                ))
        );
        StoredEvent initial = StoredEvent.received(payload.eventId(), payload, receivedAt);
        StoredEvent updated = initial.markProcessed(
                        "NEEDS_CONFIRMATION",
                        "等待用户确认草稿。",
                        "social_reply",
                        "CONFIRM_REQUIRED",
                        true,
                        Instant.parse("2026-07-10T08:00:02Z"),
                        "你好，下午两点见。",
                        trace
                )
                .markInboxStatus(
                        "SNOOZED",
                        Instant.parse("2026-07-10T09:00:00Z"),
                        Instant.parse("2026-07-10T08:00:03Z")
                );

        repository.save(initial);
        repository.save(updated);

        StoredEvent reloaded = repository.findByEventId(payload.eventId()).orElseThrow();
        assertTrue(repository.exists(payload.eventId()));
        assertEquals("下午两点开会", reloaded.payload().text());
        assertEquals("jdbc-test", reloaded.payload().rawPayload().path("source").asText());
        assertEquals("NEEDS_CONFIRMATION", reloaded.processingStatus());
        assertEquals("你好，下午两点见。", reloaded.replyDraft());
        assertEquals("SNOOZED", reloaded.inboxStatus());
        assertEquals(Instant.parse("2026-07-10T09:00:00Z"), reloaded.snoozedUntil());
        assertNotNull(reloaded.executionTrace());
        assertEquals("execution-jdbc-1", reloaded.executionTrace().executionId());
        assertEquals(List.of("send_qq_message"), reloaded.executionTrace().steps().get(0).toolNames());
    }
}
