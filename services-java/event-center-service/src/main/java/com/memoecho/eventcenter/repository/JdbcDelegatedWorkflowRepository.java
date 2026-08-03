package com.memoecho.eventcenter.repository;

import com.memoecho.eventcenter.model.DelegatedWorkflow;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.stereotype.Repository;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Timestamp;
import java.util.List;
import java.util.Optional;

/** 使用参数化 SQL 持久化主控台父工作流。 */
@Repository
public class JdbcDelegatedWorkflowRepository {

    private final JdbcTemplate jdbcTemplate;
    private final RowMapper<DelegatedWorkflow> rowMapper = new DelegatedWorkflowRowMapper();

    /** 注入 JDBC 访问器。 */
    public JdbcDelegatedWorkflowRepository(JdbcTemplate jdbcTemplate) {
        this.jdbcTemplate = jdbcTemplate;
    }

    /** 新建父工作流，步骤由同一事务中的任务仓储写入。 */
    public DelegatedWorkflow insert(DelegatedWorkflow workflow) {
        jdbcTemplate.update("""
                INSERT INTO delegated_workflow (
                    id, user_id, source_execution_id, original_command, title, workflow_type,
                    status, plan_json, facts_json, progress_summary, failure_reason,
                    created_at, updated_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, workflow.id(), workflow.userId(), blankToNull(workflow.sourceExecutionId()),
                workflow.originalCommand(), workflow.title(), workflow.workflowType(), workflow.status(),
                workflow.planJson(), workflow.factsJson(), workflow.progressSummary(), workflow.failureReason(),
                Timestamp.from(workflow.createdAt()), Timestamp.from(workflow.updatedAt()),
                workflow.completedAt() == null ? null : Timestamp.from(workflow.completedAt()));
        return workflow;
    }

    /** 按工作流 ID 和用户 ID 查询，防止跨账号读取执行计划。 */
    public Optional<DelegatedWorkflow> findByIdAndUserId(String id, String userId) {
        return jdbcTemplate.query(
                "SELECT * FROM delegated_workflow WHERE id = ? AND user_id = ?",
                rowMapper, id, userId).stream().findFirst();
    }

    /** 按主控台执行 ID 查询，用于 HTTP 重试和客户端重复点击的幂等复用。 */
    public Optional<DelegatedWorkflow> findBySourceExecutionIdAndUserId(String executionId, String userId) {
        if (executionId == null || executionId.isBlank()) {
            return Optional.empty();
        }
        return jdbcTemplate.query("""
                SELECT * FROM delegated_workflow
                WHERE source_execution_id = ? AND user_id = ?
                LIMIT 1
                """, rowMapper, executionId.trim(), userId).stream().findFirst();
    }

    /** 查询当前用户最近的父工作流，供客户端按一条命令展示一张卡片。 */
    public List<DelegatedWorkflow> findRecentByUserId(String userId, int limit) {
        int safeLimit = Math.min(Math.max(limit, 1), 100);
        return jdbcTemplate.query("""
                SELECT * FROM delegated_workflow
                WHERE user_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """, rowMapper, userId, safeLimit);
    }

    private static String blankToNull(String value) {
        return value == null || value.isBlank() ? null : value.trim();
    }

    /** 将数据库行转换成不可变领域对象。 */
    private static final class DelegatedWorkflowRowMapper implements RowMapper<DelegatedWorkflow> {
        @Override
        public DelegatedWorkflow mapRow(ResultSet rs, int rowNum) throws SQLException {
            Timestamp completedAt = rs.getTimestamp("completed_at");
            return new DelegatedWorkflow(
                    rs.getString("id"), rs.getString("user_id"), rs.getString("source_execution_id"),
                    rs.getString("original_command"), rs.getString("title"), rs.getString("workflow_type"),
                    rs.getString("status"), rs.getString("plan_json"), rs.getString("facts_json"),
                    rs.getString("progress_summary"), rs.getString("failure_reason"),
                    rs.getTimestamp("created_at").toInstant(), rs.getTimestamp("updated_at").toInstant(),
                    completedAt == null ? null : completedAt.toInstant());
        }
    }
}
