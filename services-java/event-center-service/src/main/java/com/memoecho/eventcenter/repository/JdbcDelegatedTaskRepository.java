package com.memoecho.eventcenter.repository;

import com.memoecho.eventcenter.model.DelegatedTask;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.stereotype.Repository;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Timestamp;
import java.time.Instant;
import java.util.List;
import java.util.Optional;

/** 使用参数化 SQL 保存委托任务，兼容 MySQL 与测试环境 H2。 */
@Repository
public class JdbcDelegatedTaskRepository {

    private final JdbcTemplate jdbcTemplate;
    private final RowMapper<DelegatedTask> rowMapper = new DelegatedTaskRowMapper();

    /** 注入 JDBC 访问器。 */
    public JdbcDelegatedTaskRepository(JdbcTemplate jdbcTemplate) {
        this.jdbcTemplate = jdbcTemplate;
    }

    /** 新建任务；更新必须走明确的状态方法，避免误覆盖原始意图。 */
    public DelegatedTask insert(DelegatedTask task) {
        jdbcTemplate.update("""
                        INSERT INTO delegated_task (
                            id, workflow_id, step_key, step_order, step_role, step_instruction,
                            depends_on_json, required_facts_json, produces_facts_json, result_json, activation_version,
                            user_id, task_type, status, original_command, source_execution_id, target_query,
                            platform, chat_type, chat_id, target_name, objective, success_criteria,
                            deadline_text, confidence, clarification_question, requires_confirmation,
                            execution_mode, progress_summary, state_json, last_event_id, start_event_id,
                            conversation_scope_json, started_at,
                            completed_at, completion_report, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                task.id(), task.workflowId(), task.stepKey(), task.stepOrder(), task.stepRole(), task.stepInstruction(),
                task.dependsOnJson(), task.requiredFactsJson(), task.producesFactsJson(), task.resultJson(),
                task.activationVersion(), task.userId(), task.taskType(), task.status(), task.originalCommand(),
                task.sourceExecutionId(), task.targetQuery(), task.platform(), task.chatType(), task.chatId(), task.targetName(),
                task.objective(), task.successCriteria(), task.deadlineText(), task.confidence(),
                task.clarificationQuestion(), task.requiresConfirmation(), task.executionMode(),
                nonNullText(task.progressSummary()), nonNullJson(task.stateJson()),
                nonNullText(task.lastEventId()), nonNullText(task.startEventId()),
                nonNullText(task.conversationScopeJson()), timestamp(task.startedAt()),
                timestamp(task.completedAt()), nonNullText(task.completionReport()), Timestamp.from(task.createdAt()),
                Timestamp.from(task.updatedAt()));
        return task;
    }

    /** 按用户读取最近任务，防止跨账号查看任务。 */
    public List<DelegatedTask> findRecentByUserId(String userId, int limit) {
        int safeLimit = Math.min(Math.max(limit, 1), 100);
        return jdbcTemplate.query("""
                        SELECT * FROM delegated_task
                        WHERE user_id = ?
                        ORDER BY created_at DESC
                        LIMIT ?
                        """, rowMapper, userId, safeLimit);
    }

    /** 按任务 ID 和用户 ID 读取，作为确认、取消等写操作的所有权校验。 */
    public Optional<DelegatedTask> findByIdAndUserId(String id, String userId) {
        return jdbcTemplate.query(
                "SELECT * FROM delegated_task WHERE id = ? AND user_id = ?",
                rowMapper,
                id,
                userId
        ).stream().findFirst();
    }

    /**
     * 读取父工作流下的全部步骤，并保持规划顺序稳定。
     * 执行器只能通过该顺序判断依赖和展示时间线，不能再把每个联系人当成互不相关的任务。
     */
    public List<DelegatedTask> findByWorkflowId(String workflowId) {
        return jdbcTemplate.query("""
                SELECT * FROM delegated_task
                WHERE workflow_id = ?
                ORDER BY step_order ASC, created_at ASC
                """, rowMapper, workflowId);
    }

    /**
     * 按主控台执行 ID 和目标会话读取任务。
     * 数据库唯一索引与该查询共同保证并发重试时返回已经创建的任务，而不是重复创建。
     */
    public Optional<DelegatedTask> findBySourceExecutionAndTarget(
            String userId,
            String sourceExecutionId,
            String platform,
            String chatType,
            String chatId
    ) {
        return jdbcTemplate.query("""
                SELECT * FROM delegated_task
                WHERE user_id = ? AND source_execution_id = ?
                  AND platform = ? AND chat_type = ? AND chat_id = ?
                ORDER BY created_at DESC
                LIMIT 1
                """, rowMapper, userId, sourceExecutionId, platform, chatType, chatId)
                .stream()
                .findFirst();
    }

    /**
     * 查找短时间内同一会话、同一原始命令创建的任务。
     * 这个方法用于抵御前端重复点击、Runtime 重试和并发事件导致的重复创建。
     */
    public Optional<DelegatedTask> findRecentDuplicateCommand(
            String userId,
            String originalCommand,
            String platform,
            String chatType,
            String chatId,
            Instant cutoff
    ) {
        return jdbcTemplate.query("""
                SELECT * FROM delegated_task
                WHERE user_id = ? AND original_command = ? AND platform = ? AND chat_type = ? AND chat_id = ?
                  AND created_at >= ?
                  AND status IN ('ACTIVE', 'WAITING_TARGET', 'PAUSED')
                ORDER BY created_at DESC
                LIMIT 1
                """, rowMapper, userId, originalCommand, platform, chatType, chatId, Timestamp.from(cutoff))
                .stream()
                .findFirst();
    }

    /** 更新生命周期状态并返回最新对象。 */
    public Optional<DelegatedTask> updateStatus(String id, String userId, String status, boolean requiresConfirmation) {
        jdbcTemplate.update("""
                UPDATE delegated_task
                SET status = ?, requires_confirmation = ?, updated_at = ?
                WHERE id = ? AND user_id = ?
                """, status, requiresConfirmation, Timestamp.from(java.time.Instant.now()), id, userId);
        return findByIdAndUserId(id, userId);
    }

    /**
     * 为旧的待选联系人任务补写唯一匹配的真实会话。
     * WHERE 中限制原状态，避免刷新页面覆盖已经被其他线程处理或取消的任务。
     */
    public Optional<DelegatedTask> bindWaitingTarget(String id, String userId, DelegatedTask resolved) {
        java.time.Instant now = java.time.Instant.now();
        jdbcTemplate.update("""
                UPDATE delegated_task
                SET status = 'ACTIVE', target_query = ?, platform = ?, chat_type = ?, chat_id = ?,
                    target_name = ?, clarification_question = '', requires_confirmation = FALSE,
                    progress_summary = ?, started_at = COALESCE(started_at, ?), updated_at = ?
                WHERE id = ? AND user_id = ? AND status = 'WAITING_TARGET'
                """, resolved.targetQuery(), resolved.platform(), resolved.chatType(), resolved.chatId(),
                resolved.targetName(), "已重新识别联系人，任务已启动",
                Timestamp.from(now), Timestamp.from(now), id, userId);
        return findByIdAndUserId(id, userId);
    }

    /** 查询某个会话唯一的活动委托，重启后 Runtime 通过它恢复稳定任务 ID 和图状态。 */
    public Optional<DelegatedTask> findActiveByConversation(
            String userId, String platform, String chatType, String chatId
    ) {
        return jdbcTemplate.query("""
                SELECT * FROM delegated_task
                WHERE user_id = ? AND platform = ? AND chat_type = ? AND chat_id = ? AND status = 'ACTIVE'
                ORDER BY updated_at DESC
                LIMIT 1
                """, rowMapper, userId, platform, chatType, chatId).stream().findFirst();
    }

    /**
     * 同一会话只允许一个主控台委托继续接管。
     * 新任务激活后，旧 ACTIVE/WAITING_TARGET/PAUSED 任务会被归档，防止历史任务被再次拉起执行。
     */
    public int cancelActiveByConversation(
            String userId,
            String platform,
            String chatType,
            String chatId,
            String exceptId,
            String reason
    ) {
        Instant now = Instant.now();
        return jdbcTemplate.update("""
                UPDATE delegated_task
                SET status = 'CANCELLED',
                    progress_summary = ?,
                    completion_report = ?,
                    completed_at = COALESCE(completed_at, ?),
                    updated_at = ?
                WHERE user_id = ? AND platform = ? AND chat_type = ? AND chat_id = ?
                  AND id <> ?
                  AND status IN ('ACTIVE', 'WAITING_TARGET', 'PAUSED')
                """, reason, reason, Timestamp.from(now), Timestamp.from(now),
                userId, platform, chatType, chatId, exceptId);
    }

    /** 幂等更新 LangGraph 运行态；完成时间只在进入终态时写入。 */
    public Optional<DelegatedTask> updateRuntimeState(
            String id, String userId, String status, String progressSummary, String stateJson,
            String lastEventId, String completionReport
    ) {
        java.time.Instant now = java.time.Instant.now();
        boolean terminal = "COMPLETED".equals(status) || "FAILED".equals(status) || "CANCELLED".equals(status);
        jdbcTemplate.update("""
                UPDATE delegated_task
                SET status = ?, progress_summary = ?, state_json = ?, last_event_id = ?,
                    completion_report = ?, started_at = COALESCE(started_at, ?),
                    completed_at = CASE WHEN ? THEN COALESCE(completed_at, ?) ELSE completed_at END,
                    requires_confirmation = FALSE, updated_at = ?
                WHERE id = ? AND user_id = ?
                """, status, nonNullText(progressSummary), nonNullJson(stateJson),
                nonNullText(lastEventId), nonNullText(completionReport),
                Timestamp.from(now), terminal, Timestamp.from(now), Timestamp.from(now), id, userId);
        return findByIdAndUserId(id, userId);
    }

    /**
     * 仅允许 ACTIVE 步骤完成一次。
     * 返回 0 表示步骤已被其他回调推进，调用方必须重新读取状态而不能重复执行副作用。
     */
    public int completeWorkflowStep(
            String workflowId,
            String stepKey,
            String userId,
            String resultJson,
            String progressSummary,
            Instant completedAt
    ) {
        return jdbcTemplate.update("""
                UPDATE delegated_task
                SET status = 'COMPLETED', result_json = ?, progress_summary = ?,
                    completion_report = ?, completed_at = ?, updated_at = ?
                WHERE workflow_id = ? AND step_key = ? AND user_id = ? AND status = 'ACTIVE'
                """, resultJson, progressSummary, progressSummary, Timestamp.from(completedAt),
                Timestamp.from(completedAt), workflowId, stepKey, userId);
    }

    /** 依赖和事实均满足时，将 BLOCKED 步骤幂等激活，并记录起点水位。 */
    public int activateWorkflowStep(
            String workflowId,
            String stepKey,
            String userId,
            String progressSummary,
            Instant startedAt,
            String startEventId
    ) {
        return jdbcTemplate.update("""
                UPDATE delegated_task
                SET status = 'ACTIVE', progress_summary = ?, started_at = COALESCE(started_at, ?),
                    start_event_id = CASE WHEN start_event_id = '' THEN ? ELSE start_event_id END,
                    activation_version = activation_version + 1, updated_at = ?
                WHERE workflow_id = ? AND step_key = ? AND user_id = ? AND status = 'BLOCKED'
                """, progressSummary, Timestamp.from(startedAt), nonNullText(startEventId),
                Timestamp.from(startedAt),
                workflowId, stepKey, userId);
    }

    /** 将数据库行恢复成领域对象。 */
    private static final class DelegatedTaskRowMapper implements RowMapper<DelegatedTask> {
        @Override
        public DelegatedTask mapRow(ResultSet rs, int rowNum) throws SQLException {
            return new DelegatedTask(
                    rs.getString("id"), rs.getString("workflow_id"), text(rs, "step_key", ""),
                    rs.getInt("step_order"), text(rs, "step_role", "ACTION"),
                    text(rs, "step_instruction", ""), text(rs, "depends_on_json", "[]"),
                    text(rs, "required_facts_json", "[]"), text(rs, "produces_facts_json", "[]"),
                    text(rs, "result_json", "{}"), rs.getLong("activation_version"),
                    rs.getString("user_id"), rs.getString("task_type"),
                    rs.getString("status"), rs.getString("original_command"), rs.getString("source_execution_id"),
                    rs.getString("target_query"),
                    rs.getString("platform"), rs.getString("chat_type"), rs.getString("chat_id"),
                    rs.getString("target_name"), rs.getString("objective"), rs.getString("success_criteria"),
                    rs.getString("deadline_text"), rs.getDouble("confidence"),
                    rs.getString("clarification_question"), rs.getBoolean("requires_confirmation"),
                    rs.getString("execution_mode"), rs.getString("progress_summary"),
                    rs.getString("state_json"), rs.getString("last_event_id"),
                    text(rs, "start_event_id", ""), text(rs, "conversation_scope_json", ""),
                    instant(rs.getTimestamp("started_at")), instant(rs.getTimestamp("completed_at")),
                    rs.getString("completion_report"),
                    rs.getTimestamp("created_at").toInstant(), rs.getTimestamp("updated_at").toInstant()
            );
        }

        /** 将迁移前历史行的 NULL 字段转换成稳定的领域默认值。 */
        private static String text(ResultSet rs, String column, String fallback) throws SQLException {
            String value = rs.getString(column);
            return value == null ? fallback : value;
        }
    }

    private static Timestamp timestamp(java.time.Instant value) {
        return value == null ? null : Timestamp.from(value);
    }

    /** 将可空文本转换为数据库可安全写入的空字符串。 */
    private static String nonNullText(String value) {
        return value == null ? "" : value;
    }

    /** 将可空或空白 JSON 转换为合法空对象，保证运行态始终可反序列化。 */
    private static String nonNullJson(String value) {
        return value == null || value.isBlank() ? "{}" : value;
    }

    private static java.time.Instant instant(Timestamp value) {
        return value == null ? null : value.toInstant();
    }
}
