package com.memoecho.eventcenter.repository;

import com.memoecho.eventcenter.model.WorkspaceThread;
import com.memoecho.eventcenter.model.WorkspaceThreadMessage;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.stereotype.Repository;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Timestamp;
import java.time.Instant;
import java.util.List;
import java.util.Optional;

/** 主控台对话线程与消息的持久化。 */
@Repository
public class JdbcWorkspaceThreadRepository {

    private final JdbcTemplate jdbcTemplate;

    private static final RowMapper<WorkspaceThread> THREAD_MAPPER = (rs, rowNum) -> new WorkspaceThread(
            rs.getString("id"),
            rs.getString("user_id"),
            rs.getString("title"),
            rs.getBoolean("pinned"),
            rs.getBoolean("archived"),
            toInstant(rs.getTimestamp("created_at")),
            toInstant(rs.getTimestamp("updated_at"))
    );

    private static final RowMapper<WorkspaceThreadMessage> MESSAGE_MAPPER = (rs, rowNum) -> new WorkspaceThreadMessage(
            rs.getString("id"),
            rs.getString("thread_id"),
            rs.getString("user_id"),
            rs.getString("role"),
            rs.getString("content"),
            rs.getString("status"),
            rs.getString("execution_id"),
            rs.getString("task_id"),
            rs.getString("workflow_id"),
            rs.getString("result_json"),
            toInstant(rs.getTimestamp("created_at"))
    );

    public JdbcWorkspaceThreadRepository(JdbcTemplate jdbcTemplate) {
        this.jdbcTemplate = jdbcTemplate;
    }

    private static Instant toInstant(Timestamp timestamp) {
        return timestamp == null ? null : timestamp.toInstant();
    }

    public WorkspaceThread insertThread(WorkspaceThread thread) {
        jdbcTemplate.update("""
                INSERT INTO workspace_thread (id, user_id, title, pinned, archived, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                thread.id(), thread.userId(), thread.title(),
                thread.pinned(), thread.archived(),
                Timestamp.from(thread.createdAt()), Timestamp.from(thread.updatedAt()));
        return thread;
    }

    public Optional<WorkspaceThread> findThreadByIdAndUserId(String id, String userId) {
        return jdbcTemplate.query("""
                        SELECT * FROM workspace_thread
                        WHERE id = ? AND user_id = ?
                        """, THREAD_MAPPER, id, userId)
                .stream()
                .findFirst();
    }

    public List<WorkspaceThread> listThreads(String userId, boolean includeArchived) {
        if (includeArchived) {
            return jdbcTemplate.query("""
                            SELECT * FROM workspace_thread
                            WHERE user_id = ?
                            ORDER BY pinned DESC, updated_at DESC
                            """, THREAD_MAPPER, userId);
        }
        return jdbcTemplate.query("""
                        SELECT * FROM workspace_thread
                        WHERE user_id = ? AND archived = FALSE
                        ORDER BY pinned DESC, updated_at DESC
                        """, THREAD_MAPPER, userId);
    }

    public WorkspaceThread updateThread(WorkspaceThread thread) {
        jdbcTemplate.update("""
                UPDATE workspace_thread
                SET title = ?, pinned = ?, archived = ?, updated_at = ?
                WHERE id = ? AND user_id = ?
                """,
                thread.title(), thread.pinned(), thread.archived(),
                Timestamp.from(thread.updatedAt()), thread.id(), thread.userId());
        return thread;
    }

    public void touchThread(String threadId, String userId, Instant now) {
        jdbcTemplate.update("""
                UPDATE workspace_thread SET updated_at = ? WHERE id = ? AND user_id = ?
                """, Timestamp.from(now), threadId, userId);
    }

    public WorkspaceThreadMessage insertMessage(WorkspaceThreadMessage message) {
        jdbcTemplate.update("""
                INSERT INTO workspace_thread_message
                    (id, thread_id, user_id, role, content, status, execution_id,
                     task_id, workflow_id, result_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                message.id(), message.threadId(), message.userId(), message.role(),
                message.content(), message.status(), message.executionId(),
                message.taskId(), message.workflowId(), message.resultJson(),
                Timestamp.from(message.createdAt()));
        return message;
    }

    public List<WorkspaceThreadMessage> listMessages(String threadId, int limit, Instant before) {
        if (before == null) {
            return jdbcTemplate.query("""
                            SELECT * FROM workspace_thread_message
                            WHERE thread_id = ?
                            ORDER BY created_at DESC
                            LIMIT ?
                            """, MESSAGE_MAPPER, threadId, limit);
        }
        return jdbcTemplate.query("""
                        SELECT * FROM workspace_thread_message
                        WHERE thread_id = ? AND created_at < ?
                        ORDER BY created_at DESC
                        LIMIT ?
                        """, MESSAGE_MAPPER, threadId, Timestamp.from(before), limit);
    }

    public Optional<WorkspaceThreadMessage> findMessageByIdAndUserId(String id, String userId) {
        return jdbcTemplate.query("""
                        SELECT * FROM workspace_thread_message
                        WHERE id = ? AND user_id = ?
                        """, MESSAGE_MAPPER, id, userId)
                .stream()
                .findFirst();
    }

    /** 更新消息状态与内容（如超时标记、执行终态回写）。 */
    public void updateMessage(WorkspaceThreadMessage message) {
        jdbcTemplate.update("""
                UPDATE workspace_thread_message
                SET content = ?, status = ?, task_id = ?, workflow_id = ?, result_json = ?
                WHERE id = ? AND user_id = ?
                """,
                message.content(), message.status(), message.taskId(), message.workflowId(),
                message.resultJson(), message.id(), message.userId());
    }

    /** 仅更新消息状态与内容（streaming 超时保护等）。 */
    public void updateMessageStatus(String id, String userId, String status, String content) {
        jdbcTemplate.update("""
                UPDATE workspace_thread_message
                SET content = ?, status = ?
                WHERE id = ? AND user_id = ?
                """, content, status, id, userId);
    }
}