package com.memoecho.eventcenter.repository;

import com.memoecho.eventcenter.model.MemoryCandidate;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.stereotype.Repository;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Timestamp;
import java.time.Instant;
import java.util.List;
import java.util.Optional;

/** 使用兼容 MySQL 和测试环境 H2 的 SQL 持久化长期记忆候选。 */
@Repository
public class JdbcMemoryCandidateRepository implements MemoryCandidateRepository {

    private static final RowMapper<MemoryCandidate> ROW_MAPPER = new MemoryCandidateRowMapper();
    private final JdbcTemplate jdbcTemplate;

    /** 注入 JDBC 模板；仓储层只处理数据映射，不判断事实是否可信。 */
    public JdbcMemoryCandidateRepository(JdbcTemplate jdbcTemplate) {
        this.jdbcTemplate = jdbcTemplate;
    }

    /** 根据 ID 是否存在选择插入或更新，避免依赖数据库特有的 UPSERT 语法。 */
    @Override
    public MemoryCandidate save(MemoryCandidate candidate) {
        Integer existing = jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM memory_candidate WHERE id = ?", Integer.class, candidate.id());
        if (existing != null && existing > 0) {
            jdbcTemplate.update("""
                            UPDATE memory_candidate SET
                                user_id = ?, subject = ?, predicate_name = ?, fact_value = ?, scope_type = ?,
                                platform = ?, scene = ?, chat_type = ?, chat_id = ?, source_event_ids_json = ?,
                                source_actor_type = ?, fact_authority = ?, confidence = ?, status = ?,
                                rejection_reason = ?, first_seen_at = ?, last_seen_at = ?, expires_at = ?, updated_at = ?
                            WHERE id = ?
                            """,
                    candidate.userId(), candidate.subject(), candidate.predicate(), candidate.value(),
                    candidate.scopeType(), candidate.platform(), candidate.scene(), candidate.chatType(),
                    candidate.chatId(), candidate.sourceEventIdsJson(), candidate.sourceActorType(),
                    candidate.factAuthority(), candidate.confidence(), candidate.status(), candidate.rejectionReason(),
                    toTimestamp(candidate.firstSeenAt()), toTimestamp(candidate.lastSeenAt()),
                    toNullableTimestamp(candidate.expiresAt()), toTimestamp(candidate.updatedAt()), candidate.id());
        } else {
            jdbcTemplate.update("""
                            INSERT INTO memory_candidate (
                                id, user_id, subject, predicate_name, fact_value, scope_type, platform, scene,
                                chat_type, chat_id, source_event_ids_json, source_actor_type, fact_authority,
                                confidence, status, rejection_reason, first_seen_at, last_seen_at, expires_at,
                                created_at, updated_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                    candidate.id(), candidate.userId(), candidate.subject(), candidate.predicate(), candidate.value(),
                    candidate.scopeType(), candidate.platform(), candidate.scene(), candidate.chatType(),
                    candidate.chatId(), candidate.sourceEventIdsJson(), candidate.sourceActorType(),
                    candidate.factAuthority(), candidate.confidence(), candidate.status(), candidate.rejectionReason(),
                    toTimestamp(candidate.firstSeenAt()), toTimestamp(candidate.lastSeenAt()),
                    toNullableTimestamp(candidate.expiresAt()), toTimestamp(candidate.createdAt()),
                    toTimestamp(candidate.updatedAt()));
        }
        return candidate;
    }

    /** 使用参数化状态查询，避免把用户输入拼接进 SQL。 */
    @Override
    public List<MemoryCandidate> findAllByUserIdAndStatus(String userId, String status) {
        if (status == null || status.isBlank()) {
            return jdbcTemplate.query(
                    "SELECT * FROM memory_candidate WHERE user_id = ? ORDER BY updated_at DESC",
                    ROW_MAPPER, userId);
        }
        return jdbcTemplate.query(
                "SELECT * FROM memory_candidate WHERE user_id = ? AND status = ? ORDER BY updated_at DESC",
                ROW_MAPPER, userId, status);
    }

    /** 使用 ID 与 userId 双重条件建立所有权边界。 */
    @Override
    public Optional<MemoryCandidate> findByIdAndUserId(String id, String userId) {
        return jdbcTemplate.query(
                "SELECT * FROM memory_candidate WHERE id = ? AND user_id = ?", ROW_MAPPER, id, userId
        ).stream().findFirst();
    }

    /** 只返回已确认且尚未过期的记忆，候选事实永远不会进入 Runtime。 */
    @Override
    public List<MemoryCandidate> findVerifiedByUserId(String userId, Instant now) {
        return jdbcTemplate.query("""
                        SELECT * FROM memory_candidate
                        WHERE user_id = ? AND status = 'VERIFIED'
                          AND (expires_at IS NULL OR expires_at > ?)
                        ORDER BY updated_at DESC
                        """, ROW_MAPPER, userId, toTimestamp(now));
    }

    /** 精确匹配规范化事实键；同一键的不同值会同时返回，由应用层识别为冲突候选。 */
    @Override
    public List<MemoryCandidate> findActiveByFactKey(
            String userId,
            String subject,
            String predicate,
            String scopeType,
            String platform,
            String scene,
            String chatType,
            String chatId
    ) {
        return jdbcTemplate.query("""
                        SELECT * FROM memory_candidate
                        WHERE user_id = ? AND subject = ? AND predicate_name = ? AND scope_type = ?
                          AND platform = ? AND scene = ? AND chat_type = ? AND chat_id = ?
                          AND status IN ('CANDIDATE', 'VERIFIED')
                        ORDER BY updated_at DESC
                        """, ROW_MAPPER, userId, subject, predicate, scopeType, platform, scene, chatType, chatId);
    }

    /** 到期处理是幂等更新，已拒绝和已过期记录不会被重复改写。 */
    @Override
    public int expireDueMemories(String userId, Instant now) {
        return jdbcTemplate.update("""
                        UPDATE memory_candidate SET status = 'EXPIRED', updated_at = ?
                        WHERE user_id = ? AND status IN ('CANDIDATE', 'VERIFIED')
                          AND expires_at IS NOT NULL AND expires_at <= ?
                        """, toTimestamp(now), userId, toTimestamp(now));
    }

    /** 只删除当前用户拥有的记录。 */
    @Override
    public int deleteByIdAndUserId(String id, String userId) {
        return jdbcTemplate.update("DELETE FROM memory_candidate WHERE id = ? AND user_id = ?", id, userId);
    }

    /** 把非空 Instant 转成 JDBC 时间戳。 */
    private static Timestamp toTimestamp(Instant instant) {
        return Timestamp.from(instant);
    }

    /** 允许无到期时间的长期事实保存为 null。 */
    private static Timestamp toNullableTimestamp(Instant instant) {
        return instant == null ? null : Timestamp.from(instant);
    }

    /** 把数据库行完整映射为候选记忆对象。 */
    private static final class MemoryCandidateRowMapper implements RowMapper<MemoryCandidate> {
        @Override
        public MemoryCandidate mapRow(ResultSet rs, int rowNum) throws SQLException {
            Timestamp expiresAt = rs.getTimestamp("expires_at");
            return new MemoryCandidate(
                    rs.getString("id"), rs.getString("user_id"), rs.getString("subject"),
                    rs.getString("predicate_name"), rs.getString("fact_value"), rs.getString("scope_type"),
                    rs.getString("platform"), rs.getString("scene"), rs.getString("chat_type"),
                    rs.getString("chat_id"), rs.getString("source_event_ids_json"),
                    rs.getString("source_actor_type"), rs.getString("fact_authority"),
                    rs.getDouble("confidence"), rs.getString("status"), rs.getString("rejection_reason"),
                    rs.getTimestamp("first_seen_at").toInstant(), rs.getTimestamp("last_seen_at").toInstant(),
                    expiresAt == null ? null : expiresAt.toInstant(), rs.getTimestamp("created_at").toInstant(),
                    rs.getTimestamp("updated_at").toInstant()
            );
        }
    }
}
