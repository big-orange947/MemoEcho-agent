package com.memoecho.eventcenter.repository;

import com.memoecho.eventcenter.model.DelegatedTaskCurrentEvent;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.stereotype.Repository;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Timestamp;
import java.time.Instant;
import java.util.Optional;

/**
 * 委托步骤 L0 当前事件的持久化仓储。
 *
 * <p>每个任务只保留最近一次入站事件。写入采用先更新后插入的方式，
 * 兼容 MySQL 与测试环境 H2；重复写入同一任务时覆盖旧事件，保证“当前事件”语义。
 */
@Repository
public class JdbcDelegatedTaskCurrentEventRepository {

    private final JdbcTemplate jdbcTemplate;
    private final RowMapper<DelegatedTaskCurrentEvent> rowMapper = (rs, rowNum) -> new DelegatedTaskCurrentEvent(
            rs.getString("task_id"),
            rs.getString("workflow_id"),
            text(rs, "step_key", ""),
            text(rs, "conversation_scope_json", ""),
            rs.getString("event_id"),
            text(rs, "event_type", ""),
            text(rs, "sender_id", ""),
            text(rs, "text", ""),
            rs.getTimestamp("occurred_at").toInstant(),
            text(rs, "payload_json", "{}"),
            rs.getTimestamp("updated_at").toInstant()
    );

    /** 注入 JDBC 访问器。 */
    public JdbcDelegatedTaskCurrentEventRepository(JdbcTemplate jdbcTemplate) {
        this.jdbcTemplate = jdbcTemplate;
    }

    /** 幂等写入任务的当前事件；同一任务再次写入时覆盖旧值。 */
    public DelegatedTaskCurrentEvent upsert(DelegatedTaskCurrentEvent event) {
        int updated = jdbcTemplate.update("""
                        UPDATE delegated_task_current_event
                        SET workflow_id = ?, step_key = ?, conversation_scope_json = ?, event_id = ?,
                            event_type = ?, sender_id = ?, text = ?, occurred_at = ?, payload_json = ?,
                            updated_at = ?
                        WHERE task_id = ?
                        """,
                event.workflowId(), event.stepKey(), event.conversationScopeJson(), event.eventId(),
                event.eventType(), event.senderId(), event.text(), Timestamp.from(event.occurredAt()),
                event.payloadJson(), Timestamp.from(event.updatedAt()), event.taskId());
        if (updated == 0) {
            jdbcTemplate.update("""
                            INSERT INTO delegated_task_current_event (
                                task_id, workflow_id, step_key, conversation_scope_json, event_id,
                                event_type, sender_id, text, occurred_at, payload_json, updated_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                    event.taskId(), event.workflowId(), event.stepKey(), event.conversationScopeJson(),
                    event.eventId(), event.eventType(), event.senderId(), event.text(),
                    Timestamp.from(event.occurredAt()), event.payloadJson(), Timestamp.from(event.updatedAt()));
        }
        return event;
    }

    /** 按任务读取当前事件；不存在时返回空，由调用方决定是否继续。 */
    public Optional<DelegatedTaskCurrentEvent> findByTaskId(String taskId) {
        return jdbcTemplate.query(
                "SELECT * FROM delegated_task_current_event WHERE task_id = ?",
                rowMapper,
                taskId
        ).stream().findFirst();
    }

    /** 将可空数据库列恢复为稳定默认值。 */
    private static String text(ResultSet rs, String column, String fallback) throws SQLException {
        String value = rs.getString(column);
        return value == null ? fallback : value;
    }
}
