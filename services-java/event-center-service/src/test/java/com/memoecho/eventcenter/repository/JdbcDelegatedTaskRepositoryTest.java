package com.memoecho.eventcenter.repository;

import com.memoecho.eventcenter.model.DelegatedTask;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.jdbc.JdbcTest;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.TestPropertySource;

import java.time.Instant;

import static org.assertj.core.api.Assertions.assertThat;

/** 验证委托任务在真实 JDBC 表中的保存、状态更新和用户隔离。 */
@JdbcTest
@TestPropertySource(properties = "spring.sql.init.mode=always")
class JdbcDelegatedTaskRepositoryTest {

    @Autowired
    private JdbcTemplate jdbcTemplate;

    private JdbcDelegatedTaskRepository repository;

    /** 每个测试使用真实 JdbcTemplate 创建仓储。 */
    @BeforeEach
    void setUp() {
        repository = new JdbcDelegatedTaskRepository(jdbcTemplate);
    }

    /** 保存后应仅对所属用户可见，并能进入 READY 状态。 */
    @Test
    void shouldPersistAndConfirmTaskWithinOwnerBoundary() {
        repository.insert(task("task-jdbc", "freeze"));

        assertThat(repository.findByIdAndUserId("task-jdbc", "another-user")).isEmpty();
        assertThat(repository.findRecentByUserId("freeze", 20)).hasSize(1);

        DelegatedTask ready = repository.updateStatus("task-jdbc", "freeze", "READY", false).orElseThrow();
        assertThat(ready.status()).isEqualTo("READY");
        assertThat(ready.requiresConfirmation()).isFalse();
    }

    /** 构造可被 H2 与 MySQL 共同持久化的任务数据。 */
    private DelegatedTask task(String id, String userId) {
        Instant now = Instant.parse("2026-07-20T08:00:00Z");
        return new DelegatedTask(
                id, userId, "REPLY_ONCE", "WAITING_CONFIRMATION", "帮我回复小号消息", "小号",
                "qq", "private", "3807050597", "小号", "回复小号", "回复草稿经用户确认",
                "", 0.88d, "请确认", true, now, now
        );
    }
}
