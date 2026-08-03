package com.memoecho.eventcenter.repository;

import com.memoecho.eventcenter.model.DelegatedTask;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.jdbc.JdbcTest;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.TestPropertySource;

import java.time.Instant;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * 验证主控台一次执行拆分为多个联系人任务时的严格幂等规则。
 *
 * <p>幂等键由用户、主控台执行 ID、平台、会话类型和会话 ID 共同组成。
 * 同一批次可以联系多个对象，但不能为同一对象重复创建任务。</p>
 */
@JdbcTest
@TestPropertySource(properties = "spring.sql.init.mode=always")
class DelegatedTaskExecutionIdempotencyTest {

    @Autowired
    private JdbcTemplate jdbcTemplate;

    private JdbcDelegatedTaskRepository repository;

    /** 每个测试都使用真实的 H2 表结构和 JDBC 仓储。 */
    @BeforeEach
    void setUp() {
        repository = new JdbcDelegatedTaskRepository(jdbcTemplate);
    }

    /** 同一执行批次、同一联系人只能持久化一条委托任务。 */
    @Test
    void shouldRejectDuplicateTaskForSameExecutionAndContact() {
        repository.insert(task("task-first", "desktop-execution-001", "3807050597", "km"));

        assertThat(repository.findBySourceExecutionAndTarget(
                "freeze", "desktop-execution-001", "qq", "private", "3807050597"
        )).get()
                .extracting(DelegatedTask::id)
                .isEqualTo("task-first");

        assertThatThrownBy(() ->
                repository.insert(task("task-duplicate", "desktop-execution-001", "3807050597", "km"))
        ).isInstanceOf(DataIntegrityViolationException.class);
    }

    /** 同一执行批次可以为不同联系人分别创建任务。 */
    @Test
    void shouldAllowDifferentContactsInSameExecution() {
        repository.insert(task("task-km", "desktop-execution-002", "3807050597", "km"));
        repository.insert(task("task-alt", "desktop-execution-002", "3969785168", "小号"));

        assertThat(repository.findBySourceExecutionAndTarget(
                "freeze", "desktop-execution-002", "qq", "private", "3807050597"
        )).get().extracting(DelegatedTask::id).isEqualTo("task-km");
        assertThat(repository.findBySourceExecutionAndTarget(
                "freeze", "desktop-execution-002", "qq", "private", "3969785168"
        )).get().extracting(DelegatedTask::id).isEqualTo("task-alt");
    }

    /** 构造包含主控台执行 ID 的最小可持久化任务。 */
    private DelegatedTask task(String id, String executionId, String chatId, String targetName) {
        Instant now = Instant.parse("2026-07-31T08:00:00Z");
        return new DelegatedTask(
                id,
                "freeze",
                "CONVERSATION_GOAL",
                "READY",
                "通知联系人今晚一起打游戏",
                executionId,
                targetName,
                "qq",
                "private",
                chatId,
                targetName,
                "确认今晚是否有空",
                "对方明确接受或拒绝",
                "今晚",
                0.95d,
                "",
                false,
                "AUTO_COMPLETE",
                "准备联系对方",
                "{}",
                "",
                null,
                null,
                "",
                now,
                now
        );
    }
}
