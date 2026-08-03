package com.memoecho.eventcenter.repository;

import org.springframework.dao.DuplicateKeyException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;

import java.sql.Timestamp;
import java.time.Duration;
import java.time.Instant;
import java.util.List;
import java.util.UUID;

/**
 * 用数据库租约串行化同一任务事件的处理。
 *
 * Runtime 只有获得租约后才能调用模型或外部发送接口；成功后标记 COMPLETED，异常则让租约超时，
 * 使后续重试可以安全接管。这比进程内集合能覆盖重启、Webhook 重投和多实例并发。
 */
@Repository
public class JdbcDelegatedTaskEventClaimRepository {

    private final JdbcTemplate jdbcTemplate;

    public JdbcDelegatedTaskEventClaimRepository(JdbcTemplate jdbcTemplate) {
        this.jdbcTemplate = jdbcTemplate;
    }

    /** 尝试获取事件租约；已完成或仍被其他执行者占用时返回 claimed=false。 */
    public EventClaim claim(String taskId, String userId, String eventId, Duration leaseDuration) {
        Instant now = Instant.now();
        String token = UUID.randomUUID().toString();
        Timestamp nowTimestamp = Timestamp.from(now);
        Timestamp leaseUntil = Timestamp.from(now.plus(leaseDuration));
        try {
            jdbcTemplate.update("""
                            INSERT INTO delegated_task_event_claim (
                                task_id, event_id, user_id, claim_status, claim_token,
                                lease_until, created_at, updated_at
                            ) VALUES (?, ?, ?, 'CLAIMED', ?, ?, ?, ?)
                            """,
                    taskId, eventId, userId, token, leaseUntil, nowTimestamp, nowTimestamp);
            return new EventClaim(true, token, "CLAIMED");
        } catch (DuplicateKeyException ignored) {
            int updated = jdbcTemplate.update("""
                            UPDATE delegated_task_event_claim
                            SET claim_status = 'CLAIMED', claim_token = ?, lease_until = ?, updated_at = ?
                            WHERE task_id = ? AND event_id = ? AND user_id = ?
                              AND claim_status = 'CLAIMED' AND lease_until < ?
                            """,
                    token, leaseUntil, nowTimestamp, taskId, eventId, userId, nowTimestamp);
            if (updated == 1) {
                return new EventClaim(true, token, "CLAIMED");
            }
        }

        List<String> statuses = jdbcTemplate.query(
                "SELECT claim_status FROM delegated_task_event_claim WHERE task_id = ? AND event_id = ? AND user_id = ?",
                (resultSet, rowNumber) -> resultSet.getString(1), taskId, eventId, userId);
        String status = statuses.isEmpty() ? "UNAVAILABLE" : statuses.getFirst();
        return new EventClaim(false, "", status);
    }

    /** 仅持有当前租约的执行者可完成事件，避免旧执行者覆盖新的重试结果。 */
    public boolean complete(String taskId, String userId, String eventId, String claimToken) {
        int updated = jdbcTemplate.update("""
                        UPDATE delegated_task_event_claim
                        SET claim_status = 'COMPLETED', lease_until = NULL, updated_at = ?
                        WHERE task_id = ? AND event_id = ? AND user_id = ?
                          AND claim_status = 'CLAIMED' AND claim_token = ?
                        """,
                Timestamp.from(Instant.now()), taskId, eventId, userId, claimToken);
        return updated == 1;
    }

    public record EventClaim(boolean claimed, String claimToken, String status) {
    }
}
