package com.memoecho.eventcenter.repository;

import com.memoecho.eventcenter.model.DelegatedTask;
import com.memoecho.eventcenter.model.DelegatedTaskCurrentEvent;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.jdbc.JdbcTest;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.TestPropertySource;

import java.time.Instant;

import static org.assertj.core.api.Assertions.assertThat;

/** 验证委托步骤 L0 当前事件在真实 JDBC 表中的写入、覆盖与读取。 */
@JdbcTest
@TestPropertySource(properties = "spring.sql.init.mode=always")
class JdbcDelegatedTaskCurrentEventRepositoryTest {

    @Autowired
    private JdbcTemplate jdbcTemplate;

    private JdbcDelegatedTaskRepository taskRepository;
    private JdbcDelegatedTaskCurrentEventRepository repository;

    /** 每个测试使用真实 JdbcTemplate 创建仓储，并先建立一条任务满足外键。 */
    @BeforeEach
    void setUp() {
        taskRepository = new JdbcDelegatedTaskRepository(jdbcTemplate);
        repository = new JdbcDelegatedTaskCurrentEventRepository(jdbcTemplate);
    }

    /** 第一次写入后可按任务读取；再次写入同一任务时覆盖为最新事件。 */
    @Test
    void shouldUpsertAndOverwriteCurrentEventPerTask() {
        Instant now = Instant.parse("2026-08-20T08:00:00Z");
        DelegatedTask task = task("task-current", "user-1");
        taskRepository.insert(task);

        repository.upsert(new DelegatedTaskCurrentEvent(
                "task-current", "workflow-1", "ask_km",
                "{\"platform\":\"qq\",\"chatType\":\"private\",\"chatId\":\"10001\"}",
                "event-1", "message", "10001", "七点半", now,
                "{\"eventId\":\"event-1\",\"text\":\"七点半\"}", now));

        DelegatedTaskCurrentEvent loaded = repository.findByTaskId("task-current").orElseThrow();
        assertThat(loaded.eventId()).isEqualTo("event-1");
        assertThat(loaded.text()).isEqualTo("七点半");
        assertThat(loaded.conversationScopeJson()).contains("\"chatId\":\"10001\"");
        assertThat(loaded.workflowId()).isEqualTo("workflow-1");
        assertThat(loaded.stepKey()).isEqualTo("ask_km");

        // 同一任务再次写入应覆盖，而不是插入第二行。
        repository.upsert(new DelegatedTaskCurrentEvent(
                "task-current", "workflow-1", "ask_km",
                "{\"platform\":\"qq\",\"chatType\":\"private\",\"chatId\":\"10001\"}",
                "event-2", "message", "10001", "改到八点", now.plusSeconds(60),
                "{\"eventId\":\"event-2\",\"text\":\"改到八点\"}", now.plusSeconds(60)));

        DelegatedTaskCurrentEvent overwritten = repository.findByTaskId("task-current").orElseThrow();
        assertThat(overwritten.eventId()).isEqualTo("event-2");
        assertThat(overwritten.text()).isEqualTo("改到八点");
    }

    /** 不存在的任务读取当前事件返回空，不抛出异常。 */
    @Test
    void shouldReturnEmptyWhenNoCurrentEvent() {
        assertThat(repository.findByTaskId("task-missing")).isEmpty();
    }

    /** 构造可被 H2 与 MySQL 共同持久化的任务数据。 */
    private DelegatedTask task(String id, String userId) {
        Instant now = Instant.parse("2026-07-20T08:00:00Z");
        return new DelegatedTask(
                id, userId, "WORKFLOW_STEP", "ACTIVE", "询问上课时间", "execution-1",
                "km", "qq", "private", "10001", "km", "获得上课时间", "收到明确时间",
                "", 0.9d, "", false, "AUTO_COMPLETE", "步骤已激活。", "{}", "",
                now, null, "", now, now
        );
    }
}
