package com.memoecho.eventcenter.service;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.memoecho.eventcenter.dto.ConversationDigestBatchResponse;
import com.memoecho.eventcenter.dto.ConversationDigestRequest;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.dao.DataAccessException;
import org.springframework.stereotype.Service;

import java.sql.Timestamp;
import java.time.Instant;
import java.util.List;
import java.util.UUID;

/** 持久化和查询慢通道生成的会话摘要批次。 */
@Service
public class ConversationDigestBatchService {

    private final JdbcTemplate jdbcTemplate;
    private final ObjectMapper objectMapper;

    public ConversationDigestBatchService(JdbcTemplate jdbcTemplate, ObjectMapper objectMapper) {
        this.jdbcTemplate = jdbcTemplate;
        this.objectMapper = objectMapper;
    }

    /** 保存一次不可变摘要批次，源事件列表用于追溯和后续重新总结。 */
    public ConversationDigestBatchResponse save(String userId, ConversationDigestRequest request) {
        String id = UUID.randomUUID().toString();
        Instant generatedAt = Instant.now();
        List<String> sourceIds = request.sourceEventIds() == null ? List.of() : List.copyOf(request.sourceEventIds());
        int messageCount = request.messageCount() == null ? sourceIds.size() : Math.max(0, request.messageCount());
        jdbcTemplate.update("""
                INSERT INTO conversation_digest_batch (
                    id, user_id, platform, chat_type, chat_id, aggregation_key, source_event_ids_json,
                    message_count, summary, happened, action_items, next_step, period_started_at, period_ended_at, generated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, id, userId, request.platform(), request.chatType(), request.chatId(), request.aggregationKey(),
                writeJson(sourceIds), messageCount, request.summary().trim(), safeText(request.happened()),
                safeText(request.actionItems()), safeText(request.nextStep()),
                toTimestamp(request.periodStartedAt()), toTimestamp(request.periodEndedAt()), Timestamp.from(generatedAt));
        return new ConversationDigestBatchResponse(id, request.platform(), request.chatType(), request.chatId(),
                request.aggregationKey(), sourceIds, messageCount, request.summary().trim(), safeText(request.happened()),
                safeText(request.actionItems()), safeText(request.nextStep()),
                request.periodStartedAt(), request.periodEndedAt(), generatedAt);
    }

    /** 按当前用户读取最近摘要，杜绝不同本地账户之间的数据串读。 */
    public List<ConversationDigestBatchResponse> list(String userId, int limit) {
        int safeLimit = Math.max(1, Math.min(limit, 200));
        try {
            return jdbcTemplate.query("""
                SELECT * FROM conversation_digest_batch
                WHERE user_id = ? ORDER BY generated_at DESC LIMIT ?
                """, (rs, rowNum) -> new ConversationDigestBatchResponse(
                rs.getString("id"), rs.getString("platform"), rs.getString("chat_type"), rs.getString("chat_id"),
                rs.getString("aggregation_key"), readJson(rs.getString("source_event_ids_json")),
                rs.getInt("message_count"), rs.getString("summary"), rs.getString("happened"),
                rs.getString("action_items"), rs.getString("next_step"),
                toInstant(rs.getTimestamp("period_started_at")), toInstant(rs.getTimestamp("period_ended_at")),
                rs.getTimestamp("generated_at").toInstant()), userId, safeLimit);
        } catch (DataAccessException exception) {
            // 兼容尚未完成三段式字段迁移的已有本地数据库，重启后会由 schema.sql 自动补齐列。
            return jdbcTemplate.query("""
                    SELECT id, platform, chat_type, chat_id, aggregation_key, source_event_ids_json,
                           message_count, summary, period_started_at, period_ended_at, generated_at
                    FROM conversation_digest_batch
                    WHERE user_id = ? ORDER BY generated_at DESC LIMIT ?
                    """, (rs, rowNum) -> new ConversationDigestBatchResponse(
                    rs.getString("id"), rs.getString("platform"), rs.getString("chat_type"), rs.getString("chat_id"),
                    rs.getString("aggregation_key"), readJson(rs.getString("source_event_ids_json")),
                    rs.getInt("message_count"), rs.getString("summary"), "", "", "",
                    toInstant(rs.getTimestamp("period_started_at")), toInstant(rs.getTimestamp("period_ended_at")),
                    rs.getTimestamp("generated_at").toInstant()), userId, safeLimit);
        }
    }

    private String writeJson(List<String> values) {
        try { return objectMapper.writeValueAsString(values); }
        catch (JsonProcessingException exception) { throw new IllegalStateException("摘要源事件序列化失败", exception); }
    }

    private List<String> readJson(String json) {
        try { return objectMapper.readValue(json, new TypeReference<>() {}); }
        catch (JsonProcessingException exception) { return List.of(); }
    }

    private Timestamp toTimestamp(Instant value) { return value == null ? null : Timestamp.from(value); }
    private Instant toInstant(Timestamp value) { return value == null ? null : value.toInstant(); }
    private String safeText(String value) { return value == null ? "" : value.trim(); }
}
