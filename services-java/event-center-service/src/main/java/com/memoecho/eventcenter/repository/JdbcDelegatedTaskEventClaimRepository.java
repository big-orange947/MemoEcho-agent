package com.memoecho.eventcenter.repository;

import org.springframework.dao.DuplicateKeyException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;

import java.sql.Timestamp;
import java.time.Duration;
import java.time.Instant;
import java.util.List;
import java.util.Map;
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
        if (updated == 1) {
            return true;
        }
        // 幂等：同一执行者（同 token）已经完成过该事件时，重复提交视为成功。
        // 事件处理闭环与 outbox 步骤执行闭环可能先后提交同一次租约，第二次不应
        // 被当成真正的 409 冲突（真正的冲突是 token 被另一执行者接管或租约不存在）。
        List<Map<String, Object>> rows = jdbcTemplate.queryForList(
                "SELECT claim_status, claim_token FROM delegated_task_event_claim "
                        + "WHERE task_id = ? AND event_id = ? AND user_id = ? LIMIT 1",
                taskId, eventId, userId);
        if (rows.size() == 1
                && "COMPLETED".equals(rows.getFirst().get("claim_status"))
                && claimToken != null
                && claimToken.equals(rows.getFirst().get("claim_token"))) {
            return true;
        }
        return false;
    }

    /**
     * 释放一个尚未产生持久副作用的事件租约。
     *
     * <p>这里直接删除 CLAIMED 记录，而不是把它标记为失败。下一轮调度仍使用同一个
     * eventId 时即可重新认领；claimToken 条件则保证过期执行者不能释放新执行者的租约。</p>
     */
    public boolean release(String taskId, String userId, String eventId, String claimToken) {
        int deleted = jdbcTemplate.update("""
                        DELETE FROM delegated_task_event_claim
                        WHERE task_id = ? AND event_id = ? AND user_id = ?
                          AND claim_status = 'CLAIMED' AND claim_token = ?
                        """,
                taskId, eventId, userId, claimToken);
        return deleted == 1;
    }

    /**
     * 恢复旧版本错误写成 COMPLETED、但实际上从未产生持久化执行结果的事件。
     *
     * <p>恢复条件刻意保持严格：任务仍处于活动状态、没有完成时间、没有最后处理事件，
     * 当前步骤及同一工作流也没有结果。任何已经真实执行过的任务都不会满足这些条件。</p>
     */
    public boolean recoverDormantCompleted(String taskId, String userId, String eventId) {
        int deleted = jdbcTemplate.update("""
                        DELETE FROM delegated_task_event_claim
                        WHERE task_id = ? AND event_id = ? AND user_id = ?
                          AND claim_status = 'COMPLETED'
                          AND EXISTS (
                              SELECT 1
                              FROM delegated_task task
                              WHERE task.id = ? AND task.user_id = ?
                                AND task.status = 'ACTIVE'
                                AND task.completed_at IS NULL
                                AND COALESCE(TRIM(task.last_event_id), '') = ''
                                AND COALESCE(TRIM(task.result_json), '') IN ('', '{}', 'null')
                                AND (
                                    task.workflow_id IS NULL
                                    OR NOT EXISTS (
                                        SELECT 1
                                        FROM delegated_task sibling
                                        WHERE sibling.workflow_id = task.workflow_id
                                          AND sibling.user_id = task.user_id
                                          AND COALESCE(TRIM(sibling.result_json), '') NOT IN ('', '{}', 'null')
                                    )
                                )
                          )
                        """,
                taskId, eventId, userId, taskId, userId);
        return deleted == 1;
    }

    public record EventClaim(boolean claimed, String claimToken, String status) {
    }
}
