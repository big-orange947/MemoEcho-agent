package com.memoecho.eventcenter.repository;

import com.memoecho.eventcenter.model.AgentDispatchRetryJob;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;

import java.sql.Timestamp;
import java.time.Instant;
import java.util.List;
import java.util.Optional;

@Repository
public class JdbcAgentDispatchRetryJobRepository implements AgentDispatchRetryJobRepository {

    private final JdbcTemplate jdbcTemplate;

    /** 注入 JDBC 访问组件。 */
    public JdbcAgentDispatchRetryJobRepository(JdbcTemplate jdbcTemplate) {
        this.jdbcTemplate = jdbcTemplate;
    }

    @Override
    public void schedule(String eventId, int attemptCount, Instant nextAttemptAt, String lastError, Instant now) {
        // 先更新后插入可同时兼容当前 H2 和后续 MySQL 数据源。
        int updated = jdbcTemplate.update("""
                        UPDATE agent_dispatch_retry
                        SET status = 'WAITING', attempt_count = ?, next_attempt_at = ?, last_error = ?, updated_at = ?
                        WHERE event_id = ?
                        """,
                attemptCount, timestamp(nextAttemptAt), safeError(lastError), timestamp(now), eventId);
        if (updated == 0) {
            jdbcTemplate.update("""
                            INSERT INTO agent_dispatch_retry (
                                event_id, status, attempt_count, next_attempt_at, last_error, created_at, updated_at
                            ) VALUES (?, 'WAITING', ?, ?, ?, ?, ?)
                            """,
                    eventId, attemptCount, timestamp(nextAttemptAt), safeError(lastError), timestamp(now), timestamp(now));
        }
    }

    @Override
    public List<AgentDispatchRetryJob> findDue(Instant now, int limit) {
        return jdbcTemplate.query("""
                        SELECT event_id, status, attempt_count, next_attempt_at, last_error, created_at, updated_at
                        FROM agent_dispatch_retry
                        WHERE status = 'WAITING' AND next_attempt_at <= ?
                        ORDER BY next_attempt_at ASC
                        LIMIT ?
                        """,
                (rs, rowNum) -> new AgentDispatchRetryJob(
                        rs.getString("event_id"),
                        rs.getString("status"),
                        rs.getInt("attempt_count"),
                        instant(rs.getTimestamp("next_attempt_at")),
                        rs.getString("last_error"),
                        instant(rs.getTimestamp("created_at")),
                        instant(rs.getTimestamp("updated_at"))
                ),
                timestamp(now), Math.max(limit, 1));
    }

    @Override
    public boolean claim(String eventId, int attemptCount, Instant now) {
        return jdbcTemplate.update("""
                        UPDATE agent_dispatch_retry
                        SET status = 'PROCESSING', updated_at = ?
                        WHERE event_id = ? AND status = 'WAITING' AND attempt_count = ?
                        """,
                timestamp(now), eventId, attemptCount) == 1;
    }

    @Override
    public void markSucceeded(String eventId, Instant now) {
        jdbcTemplate.update("""
                        UPDATE agent_dispatch_retry
                        SET status = 'SUCCEEDED', next_attempt_at = NULL, last_error = '', updated_at = ?
                        WHERE event_id = ?
                        """,
                timestamp(now), eventId);
    }

    @Override
    public void markDead(String eventId, int attemptCount, String lastError, Instant now) {
        jdbcTemplate.update("""
                        UPDATE agent_dispatch_retry
                        SET status = 'DEAD', attempt_count = ?, next_attempt_at = NULL, last_error = ?, updated_at = ?
                        WHERE event_id = ?
                        """,
                attemptCount, safeError(lastError), timestamp(now), eventId);
    }

    @Override
    public Optional<AgentDispatchRetryJob> findByEventId(String eventId) {
        return jdbcTemplate.query("""
                        SELECT event_id, status, attempt_count, next_attempt_at, last_error, created_at, updated_at
                        FROM agent_dispatch_retry WHERE event_id = ?
                        """,
                (rs, rowNum) -> new AgentDispatchRetryJob(
                        rs.getString("event_id"),
                        rs.getString("status"),
                        rs.getInt("attempt_count"),
                        instant(rs.getTimestamp("next_attempt_at")),
                        rs.getString("last_error"),
                        instant(rs.getTimestamp("created_at")),
                        instant(rs.getTimestamp("updated_at"))
                ),
                eventId).stream().findFirst();
    }

    /** 限制数据库错误字段长度，避免下游返回整段响应撑大记录。 */
    private String safeError(String error) {
        if (error == null || error.isBlank()) {
            return "";
        }
        return error.length() <= 2000 ? error : error.substring(0, 2000);
    }

    /** 把 Instant 转成可空 JDBC 时间。 */
    private Timestamp timestamp(Instant instant) {
        return instant == null ? null : Timestamp.from(instant);
    }

    /** 把 JDBC 时间还原为可空 Instant。 */
    private Instant instant(Timestamp timestamp) {
        return timestamp == null ? null : timestamp.toInstant();
    }
}
