package com.memoecho.eventcenter.repository;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.memoecho.eventcenter.dto.UnifiedEventPayload;
import com.memoecho.eventcenter.model.ExecutionTrace;
import com.memoecho.eventcenter.model.StoredEvent;
import org.springframework.context.annotation.Primary;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.stereotype.Repository;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Timestamp;
import java.time.Instant;
import java.util.List;
import java.util.Optional;

@Primary
@Repository
public class JdbcEventRecordRepository implements EventRecordRepository {

    private final JdbcTemplate jdbcTemplate;
    private final ObjectMapper objectMapper;

    public JdbcEventRecordRepository(JdbcTemplate jdbcTemplate, ObjectMapper objectMapper) {
        // 这个构造函数的作用是注入数据库访问组件和 JSON 序列化器，用于持久化统一事件与脱敏执行轨迹。
        this.jdbcTemplate = jdbcTemplate;
        this.objectMapper = objectMapper;
    }

    @Override
    public boolean exists(String eventId) {
        // 这个函数的作用是按事件 ID 判断是否已入库，继续沿用事件中心已有的幂等处理逻辑。
        Integer count = jdbcTemplate.queryForObject(
                "SELECT COUNT(1) FROM event_record WHERE event_id = ?",
                Integer.class,
                eventId
        );
        return count != null && count > 0;
    }

    @Override
    public void save(StoredEvent event) {
        // 这个函数的作用是保存完整事件状态；先更新再插入的方式兼容 H2 和 MySQL，避免依赖方言专属 MERGE 语法。
        int updated = jdbcTemplate.update("""
                        UPDATE event_record
                        SET payload_json = ?, received_at = ?, processing_status = ?, processing_summary = ?,
                            resolved_route = ?, write_back_status = ?, need_human_confirmation = ?, processed_at = ?,
                            reply_draft = ?, execution_trace_json = ?, last_action = ?, last_action_note = ?,
                            last_action_at = ?, inbox_status = ?, inbox_updated_at = ?, snoozed_until = ?
                        WHERE event_id = ?
                        """,
                serialize(event.payload()),
                toTimestamp(event.receivedAt()),
                event.processingStatus(),
                event.processingSummary(),
                event.resolvedRoute(),
                event.writeBackStatus(),
                event.needHumanConfirmation(),
                toTimestamp(event.processedAt()),
                event.replyDraft(),
                serializeNullable(event.executionTrace()),
                event.lastAction(),
                event.lastActionNote(),
                toTimestamp(event.lastActionAt()),
                event.inboxStatus(),
                toTimestamp(event.inboxUpdatedAt()),
                toTimestamp(event.snoozedUntil()),
                event.eventId()
        );
        if (updated == 0) {
            jdbcTemplate.update("""
                            INSERT INTO event_record (
                                event_id, payload_json, received_at, processing_status, processing_summary,
                                resolved_route, write_back_status, need_human_confirmation, processed_at,
                                reply_draft, execution_trace_json, last_action, last_action_note, last_action_at,
                                inbox_status, inbox_updated_at, snoozed_until
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                    event.eventId(),
                    serialize(event.payload()),
                    toTimestamp(event.receivedAt()),
                    event.processingStatus(),
                    event.processingSummary(),
                    event.resolvedRoute(),
                    event.writeBackStatus(),
                    event.needHumanConfirmation(),
                    toTimestamp(event.processedAt()),
                    event.replyDraft(),
                    serializeNullable(event.executionTrace()),
                    event.lastAction(),
                    event.lastActionNote(),
                    toTimestamp(event.lastActionAt()),
                    event.inboxStatus(),
                    toTimestamp(event.inboxUpdatedAt()),
                    toTimestamp(event.snoozedUntil())
            );
        }
    }

    @Override
    public Optional<StoredEvent> findByEventId(String eventId) {
        // 这个函数的作用是按事件 ID 读取完整状态，用于草稿确认、收件箱操作和执行轨迹查看。
        List<StoredEvent> result = jdbcTemplate.query(
                "SELECT * FROM event_record WHERE event_id = ?",
                rowMapper(),
                eventId
        );
        return result.stream().findFirst();
    }

    @Override
    public List<StoredEvent> findAll() {
        // 这个函数的作用是按接收时间倒序读取事件，保证会话聚合和工作台优先看到最新消息。
        return jdbcTemplate.query(
                "SELECT * FROM event_record ORDER BY received_at DESC",
                rowMapper()
        );
    }

    private RowMapper<StoredEvent> rowMapper() {
        // 这个函数的作用是创建依赖当前 ObjectMapper 的行映射器，负责把 JSON 列还原成领域对象。
        return (rs, rowNum) -> new StoredEvent(
                rs.getString("event_id"),
                deserialize(rs.getString("payload_json"), UnifiedEventPayload.class),
                toInstant(rs.getTimestamp("received_at")),
                rs.getString("processing_status"),
                rs.getString("processing_summary"),
                rs.getString("resolved_route"),
                rs.getString("write_back_status"),
                rs.getBoolean("need_human_confirmation"),
                toInstant(rs.getTimestamp("processed_at")),
                rs.getString("reply_draft"),
                deserializeNullable(rs.getString("execution_trace_json"), ExecutionTrace.class),
                rs.getString("last_action"),
                rs.getString("last_action_note"),
                toInstant(rs.getTimestamp("last_action_at")),
                rs.getString("inbox_status"),
                toInstant(rs.getTimestamp("inbox_updated_at")),
                toInstant(rs.getTimestamp("snoozed_until"))
        );
    }

    private String serialize(Object value) {
        // 这个函数的作用是把必填 JSON 领域对象序列化为数据库文本；失败时中断写入，避免保存半损坏事件。
        try {
            return objectMapper.writeValueAsString(value);
        } catch (JsonProcessingException ex) {
            throw new IllegalStateException("事件 JSON 序列化失败。", ex);
        }
    }

    private String serializeNullable(Object value) {
        // 这个函数的作用是处理可选执行轨迹，未产生 Runtime 响应时保持数据库列为空。
        return value == null ? null : serialize(value);
    }

    private <T> T deserialize(String json, Class<T> type) throws SQLException {
        // 这个函数的作用是把必填 JSON 列还原为领域对象；损坏数据会以 SQLException 形式暴露，方便数据库问题定位。
        try {
            return objectMapper.readValue(json, type);
        } catch (JsonProcessingException ex) {
            throw new SQLException("事件 JSON 反序列化失败。", ex);
        }
    }

    private <T> T deserializeNullable(String json, Class<T> type) throws SQLException {
        // 这个函数的作用是处理可空 JSON 列，执行轨迹不存在时不强行构造空对象。
        return json == null || json.isBlank() ? null : deserialize(json, type);
    }

    private static Timestamp toTimestamp(Instant instant) {
        // 这个函数的作用是把可空 Instant 转换成 JDBC Timestamp，便于记录状态变化时间。
        return instant == null ? null : Timestamp.from(instant);
    }

    private static Instant toInstant(Timestamp timestamp) {
        // 这个函数的作用是把数据库可空 Timestamp 还原成 Instant，保持 API 时间字段统一使用 UTC。
        return timestamp == null ? null : timestamp.toInstant();
    }
}
