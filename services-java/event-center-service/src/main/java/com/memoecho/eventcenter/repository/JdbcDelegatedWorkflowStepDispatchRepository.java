package com.memoecho.eventcenter.repository;

import com.memoecho.eventcenter.model.DelegatedWorkflowStepDispatch;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;

import java.sql.Timestamp;
import java.time.Instant;
import java.util.List;
import java.util.Optional;

/** 持久化工作流步骤投递，并通过条件更新提供多实例安全的租约。 */
@Repository
public class JdbcDelegatedWorkflowStepDispatchRepository {

    private final JdbcTemplate jdbcTemplate;

    public JdbcDelegatedWorkflowStepDispatchRepository(JdbcTemplate jdbcTemplate) {
        this.jdbcTemplate = jdbcTemplate;
    }

    /** 在业务事务内幂等写入步骤投递，保证步骤激活与待执行记录同时提交。 */
    public void enqueue(
            String workflowId,
            String stepKey,
            long activationVersion,
            String taskId,
            String userId,
            Instant now
    ) {
        jdbcTemplate.update("""
                INSERT IGNORE INTO delegated_workflow_step_dispatch (
                    workflow_id, step_key, activation_version, task_id, user_id,
                    status, attempt_count, next_attempt_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'PENDING', 0, ?, ?, ?)
                """, workflowId, stepKey, activationVersion, taskId, userId,
                Timestamp.from(now), Timestamp.from(now), Timestamp.from(now));
    }

    /**
     * 为已经激活但缺少投递记录的步骤补写 outbox。
     *
     * <p>工作流步骤和投递表之间采用数据库唯一键保证幂等，因此该方法可以周期执行。
     * 它既能修复服务升级前创建的任务，也能恢复业务事务已经提交、投递记录却意外缺失的任务。</p>
     */
    public int enqueueMissingActiveSteps(Instant now) {
        Timestamp timestamp = Timestamp.from(now);
        return jdbcTemplate.update("""
                INSERT IGNORE INTO delegated_workflow_step_dispatch (
                    workflow_id, step_key, activation_version, task_id, user_id,
                    status, attempt_count, next_attempt_at, created_at, updated_at
                )
                SELECT task.workflow_id, task.step_key, task.activation_version,
                       task.id, task.user_id,
                       'PENDING', 0, ?, ?, ?
                FROM delegated_task task
                INNER JOIN delegated_workflow workflow
                    ON workflow.id = task.workflow_id
                   AND workflow.user_id = task.user_id
                WHERE task.status = 'ACTIVE'
                  AND workflow.status = 'RUNNING'
                  AND task.workflow_id IS NOT NULL
                  AND task.workflow_id <> ''
                  AND task.step_key IS NOT NULL
                  AND task.step_key <> ''
                """, timestamp, timestamp, timestamp);
    }

    /** 返回当前到期或租约过期的候选 ID；真正抢占仍由条件更新决定。 */
    public List<Long> findDueIds(Instant now, int limit) {
        return jdbcTemplate.queryForList("""
                SELECT id FROM delegated_workflow_step_dispatch
                WHERE (status = 'PENDING' AND next_attempt_at <= ?)
                   OR (status = 'PROCESSING' AND lease_until <= ?)
                ORDER BY next_attempt_at ASC, id ASC
                LIMIT ?
                """, Long.class, Timestamp.from(now), Timestamp.from(now), limit);
    }

    /** 尝试抢占一个候选并建立租约；返回 0 表示已被其他实例抢走。 */
    public int claim(long id, Instant now, Instant leaseUntil) {
        return jdbcTemplate.update("""
                UPDATE delegated_workflow_step_dispatch
                SET status = 'PROCESSING', attempt_count = attempt_count + 1,
                    lease_until = ?, updated_at = ?
                WHERE id = ? AND (
                    (status = 'PENDING' AND next_attempt_at <= ?)
                    OR (status = 'PROCESSING' AND lease_until <= ?)
                )
                """, Timestamp.from(leaseUntil), Timestamp.from(now), id,
                Timestamp.from(now), Timestamp.from(now));
    }

    /** 按主键读取已抢占的完整投递内容。 */
    public Optional<DelegatedWorkflowStepDispatch> findById(long id) {
        return jdbcTemplate.query("""
                SELECT id, workflow_id, step_key, activation_version, task_id, user_id,
                       status, attempt_count, next_attempt_at, lease_until, last_error
                FROM delegated_workflow_step_dispatch WHERE id = ?
                """, (rs, rowNum) -> new DelegatedWorkflowStepDispatch(
                rs.getLong("id"), rs.getString("workflow_id"), rs.getString("step_key"),
                rs.getLong("activation_version"), rs.getString("task_id"), rs.getString("user_id"),
                rs.getString("status"), rs.getInt("attempt_count"),
                rs.getTimestamp("next_attempt_at").toInstant(),
                rs.getTimestamp("lease_until") == null ? null : rs.getTimestamp("lease_until").toInstant(),
                rs.getString("last_error")), id).stream().findFirst();
    }

    /** 标记投递成功；过期租约持有者不能覆盖新一轮执行结果。 */
    public int markSucceeded(long id, int expectedAttemptCount, Instant now) {
        return jdbcTemplate.update("""
                UPDATE delegated_workflow_step_dispatch
                SET status = 'SUCCEEDED', lease_until = NULL, last_error = NULL,
                    completed_at = ?, updated_at = ?
                WHERE id = ? AND status = 'PROCESSING' AND attempt_count = ?
                """, Timestamp.from(now), Timestamp.from(now), id, expectedAttemptCount);
    }

    /** 释放失败投递并设置下一次退避时间。 */
    public int scheduleRetry(
            long id,
            int expectedAttemptCount,
            Instant nextAttemptAt,
            String error,
            Instant now
    ) {
        return jdbcTemplate.update("""
                UPDATE delegated_workflow_step_dispatch
                SET status = 'PENDING', next_attempt_at = ?, lease_until = NULL,
                    last_error = ?, updated_at = ?
                WHERE id = ? AND status = 'PROCESSING' AND attempt_count = ?
                """, Timestamp.from(nextAttemptAt), error, Timestamp.from(now), id, expectedAttemptCount);
    }
}
