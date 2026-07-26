package com.memoecho.eventcenter.repository;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.memoecho.eventcenter.model.ConversationProxyTaskState;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.stereotype.Repository;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Timestamp;
import java.time.Instant;
import java.util.List;
import java.util.Optional;

/** 使用参数化 SQL 持久化每个会话的代理任务状态。 */
@Repository
public class JdbcConversationProxyTaskStateRepository {

    private final JdbcTemplate jdbcTemplate;
    private final ObjectMapper objectMapper;
    private final RowMapper<ConversationProxyTaskState> rowMapper = new StateRowMapper();

    /** 注入数据库访问器和 JSON 编解码器。 */
    public JdbcConversationProxyTaskStateRepository(JdbcTemplate jdbcTemplate, ObjectMapper objectMapper) {
        this.jdbcTemplate = jdbcTemplate;
        this.objectMapper = objectMapper;
    }

    /** 按设定和会话读取唯一状态。 */
    public Optional<ConversationProxyTaskState> find(String profileId, String chatId) {
        return jdbcTemplate.query(
                "SELECT * FROM conversation_proxy_task_state WHERE profile_id = ? AND chat_id = ?",
                rowMapper, profileId, chatId
        ).stream().findFirst();
    }

    /** 读取当前用户所有等待审批的结束申请。 */
    public List<ConversationProxyTaskState> findPendingByUserId(String userId) {
        return jdbcTemplate.query("""
                SELECT * FROM conversation_proxy_task_state
                WHERE user_id = ? AND status = 'COMPLETION_REQUESTED'
                ORDER BY requested_at DESC
                """, rowMapper, userId);
    }

    /** 新建任务状态；只在首次命中或任务定义变更后调用。 */
    public ConversationProxyTaskState insert(ConversationProxyTaskState state) {
        jdbcTemplate.update("""
                INSERT INTO conversation_proxy_task_state (
                    profile_id, user_id, platform, chat_type, chat_id, objective_hash, status,
                    completion_summary, completion_reason, completion_evidence_json,
                    requested_at, decided_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, state.profileId(), state.userId(), state.platform(), state.chatType(), state.chatId(),
                state.objectiveHash(), state.status(), state.completionSummary(), state.completionReason(),
                writeJson(state.completionEvidence()), timestamp(state.requestedAt()), timestamp(state.decidedAt()),
                Timestamp.from(state.createdAt()), Timestamp.from(state.updatedAt()));
        return state;
    }

    /** 任务定义改变时重置原状态，防止旧任务的完成结论污染新任务。 */
    public ConversationProxyTaskState reset(ConversationProxyTaskState state) {
        jdbcTemplate.update("""
                UPDATE conversation_proxy_task_state
                SET user_id = ?, platform = ?, chat_type = ?, objective_hash = ?, status = 'ACTIVE',
                    completion_summary = '', completion_reason = '', completion_evidence_json = '[]',
                    requested_at = NULL, decided_at = NULL, updated_at = ?
                WHERE profile_id = ? AND chat_id = ?
                """, state.userId(), state.platform(), state.chatType(), state.objectiveHash(),
                Timestamp.from(state.updatedAt()), state.profileId(), state.chatId());
        return find(state.profileId(), state.chatId()).orElseThrow();
    }

    /** 保存 Runtime 提交的完成申请；重复申请只更新证据，不创建重复记录。 */
    public ConversationProxyTaskState requestCompletion(
            String profileId, String chatId, String summary, String reason, List<String> evidence
    ) {
        Instant now = Instant.now();
        jdbcTemplate.update("""
                UPDATE conversation_proxy_task_state
                SET status = 'COMPLETION_REQUESTED', completion_summary = ?, completion_reason = ?,
                    completion_evidence_json = ?, requested_at = ?, decided_at = NULL, updated_at = ?
                WHERE profile_id = ? AND chat_id = ? AND status <> 'COMPLETED'
                """, summary, reason, writeJson(evidence), Timestamp.from(now), Timestamp.from(now), profileId, chatId);
        return find(profileId, chatId).orElseThrow();
    }

    /** 批准时结束代理，拒绝时恢复任务推进。 */
    public ConversationProxyTaskState decide(String profileId, String chatId, boolean approved) {
        Instant now = Instant.now();
        jdbcTemplate.update("""
                UPDATE conversation_proxy_task_state
                SET status = ?, decided_at = ?, updated_at = ?
                WHERE profile_id = ? AND chat_id = ? AND status = 'COMPLETION_REQUESTED'
                """, approved ? "COMPLETED" : "ACTIVE", Timestamp.from(now), Timestamp.from(now), profileId, chatId);
        return find(profileId, chatId).orElseThrow();
    }

    /** 将证据列表编码为 JSON，避免手工拼接破坏内容。 */
    private String writeJson(List<String> values) {
        try {
            return objectMapper.writeValueAsString(values == null ? List.of() : values);
        } catch (JsonProcessingException exception) {
            throw new IllegalArgumentException("任务完成证据无法序列化", exception);
        }
    }

    /** 将可空时间转换为 JDBC 时间。 */
    private static Timestamp timestamp(Instant value) {
        return value == null ? null : Timestamp.from(value);
    }

    /** 将数据库行恢复为领域对象。 */
    private final class StateRowMapper implements RowMapper<ConversationProxyTaskState> {
        @Override
        public ConversationProxyTaskState mapRow(ResultSet rs, int rowNum) throws SQLException {
            try {
                return new ConversationProxyTaskState(
                        rs.getString("profile_id"), rs.getString("user_id"), rs.getString("platform"),
                        rs.getString("chat_type"), rs.getString("chat_id"), rs.getString("objective_hash"),
                        rs.getString("status"), rs.getString("completion_summary"), rs.getString("completion_reason"),
                        objectMapper.readValue(rs.getString("completion_evidence_json"), new TypeReference<>() {}),
                        instant(rs.getTimestamp("requested_at")), instant(rs.getTimestamp("decided_at")),
                        rs.getTimestamp("created_at").toInstant(), rs.getTimestamp("updated_at").toInstant()
                );
            } catch (JsonProcessingException exception) {
                throw new SQLException("任务完成证据无法解析", exception);
            }
        }
    }

    /** 将可空 JDBC 时间恢复为 Instant。 */
    private static Instant instant(Timestamp value) {
        return value == null ? null : value.toInstant();
    }
}
