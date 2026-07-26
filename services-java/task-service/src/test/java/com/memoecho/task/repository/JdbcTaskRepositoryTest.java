package com.memoecho.task.repository;

import com.memoecho.task.model.TaskItem;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.jdbc.JdbcTest;
import org.springframework.context.annotation.Import;

import java.time.LocalDateTime;

import static org.assertj.core.api.Assertions.assertThat;

@JdbcTest
@Import(JdbcTaskRepository.class)
class JdbcTaskRepositoryTest {

    @Autowired
    private JdbcTaskRepository repository;

    @Test
    void shouldPersistAndReloadCompleteTask() {
        // 验证任务跨仓储读写后所有字段保持一致，不再依赖当前 Java 进程中的对象引用。
        TaskItem item = task("task-persisted", "source-persisted", "准备项目汇报");

        repository.save(item);

        assertThat(repository.findBySourceEventId("source-persisted")).contains(item);
        assertThat(repository.findAll()).contains(item);
    }

    @Test
    void shouldReturnExistingTaskWhenSourceEventIsInsertedAgain() {
        // 验证同一来源事件重复消费时复用首条任务，数据库中不会出现重复记录。
        TaskItem first = task("task-first", "same-source", "首条任务");
        TaskItem duplicate = task("task-duplicate", "same-source", "重复任务");

        repository.save(first);
        TaskItem resolved = repository.save(duplicate);

        assertThat(resolved).isEqualTo(first);
        assertThat(repository.findAll().stream()
                .filter(item -> item.sourceEventId().equals("same-source")))
                .hasSize(1);
    }

    /** 创建包含中文文本和截止时间的标准测试任务。 */
    private TaskItem task(String id, String sourceEventId, String title) {
        LocalDateTime createdAt = LocalDateTime.of(2026, 7, 16, 9, 0);
        return new TaskItem(
                id,
                sourceEventId,
                "qq",
                "1098307542",
                "2597164807",
                title,
                "完成任务并同步处理结果",
                createdAt.plusHours(4),
                "high",
                "pending",
                "high",
                createdAt
        );
    }
}
