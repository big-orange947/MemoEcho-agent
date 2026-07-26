package com.memoecho.task.repository;

import com.memoecho.task.model.TaskItem;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.stereotype.Repository;

import java.sql.Timestamp;
import java.time.LocalDateTime;
import java.util.List;
import java.util.Optional;

@Repository
public class JdbcTaskRepository implements TaskRepository {

    private final JdbcTemplate jdbcTemplate;

    /** 注入 Spring JDBC 入口，所有任务读写都通过数据库连接池完成。 */
    public JdbcTaskRepository(JdbcTemplate jdbcTemplate) {
        this.jdbcTemplate = jdbcTemplate;
    }

    @Override
    public TaskItem save(TaskItem item) {
        // source_event_id 的唯一约束是最终幂等防线，可覆盖两个并发请求同时通过业务层预查询的情况。
        try {
            jdbcTemplate.update("""
                            INSERT INTO task_item (
                                id, source_event_id, platform, chat_id, sender_id, title,
                                description, due_time, priority, status, confidence, created_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                    item.id(),
                    item.sourceEventId(),
                    item.platform(),
                    item.chatId(),
                    item.senderId(),
                    item.title(),
                    item.description(),
                    timestamp(item.dueTime()),
                    item.priority(),
                    item.status(),
                    item.confidence(),
                    timestamp(item.createdAt())
            );
            return item;
        } catch (DuplicateKeyException exception) {
            return findBySourceEventId(item.sourceEventId()).orElseThrow(() -> exception);
        }
    }

    @Override
    public List<TaskItem> findAll() {
        // 先按创建时间倒序读取，业务层会继续依据状态、截止时间和优先级完成最终排序。
        return jdbcTemplate.query(
                "SELECT * FROM task_item ORDER BY created_at DESC",
                taskRowMapper()
        );
    }

    @Override
    public Optional<TaskItem> findBySourceEventId(String sourceEventId) {
        // 按来源事件读取任务，供事件重试和重复消费时直接复用已有记录。
        return jdbcTemplate.query(
                        "SELECT * FROM task_item WHERE source_event_id = ?",
                        taskRowMapper(),
                        sourceEventId
                ).stream()
                .findFirst();
    }

    /** 集中维护数据库列到任务领域模型的映射，避免不同查询出现字段语义偏差。 */
    private RowMapper<TaskItem> taskRowMapper() {
        return (resultSet, rowNumber) -> new TaskItem(
                resultSet.getString("id"),
                resultSet.getString("source_event_id"),
                resultSet.getString("platform"),
                resultSet.getString("chat_id"),
                resultSet.getString("sender_id"),
                resultSet.getString("title"),
                resultSet.getString("description"),
                localDateTime(resultSet.getTimestamp("due_time")),
                resultSet.getString("priority"),
                resultSet.getString("status"),
                resultSet.getString("confidence"),
                localDateTime(resultSet.getTimestamp("created_at"))
        );
    }

    /** 将可空 LocalDateTime 转成 JDBC Timestamp。 */
    private Timestamp timestamp(LocalDateTime value) {
        return value == null ? null : Timestamp.valueOf(value);
    }

    /** 将可空 JDBC Timestamp 还原成 LocalDateTime。 */
    private LocalDateTime localDateTime(Timestamp value) {
        return value == null ? null : value.toLocalDateTime();
    }
}
