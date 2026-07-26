package com.memoecho.schedule.repository;

import com.memoecho.schedule.model.ScheduleItem;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.stereotype.Repository;

import java.sql.Timestamp;
import java.time.LocalDateTime;
import java.util.List;
import java.util.Optional;

@Repository
public class JdbcScheduleRepository implements ScheduleRepository {

    private final JdbcTemplate jdbcTemplate;

    public JdbcScheduleRepository(JdbcTemplate jdbcTemplate) {
        // 这个构造函数的作用是注入 Spring JDBC 入口，所有 SQL 都通过连接池执行。
        this.jdbcTemplate = jdbcTemplate;
    }

    @Override
    public ScheduleItem save(ScheduleItem item) {
        // 这个函数的作用是插入一条完整日程；source_event_id 的唯一约束负责数据库级幂等保护。
        jdbcTemplate.update("""
                        INSERT INTO schedule_item (
                            id, source_event_id, platform, chat_id, sender_id, title,
                            start_time, end_time, location, content, participants, confidence, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                item.id(),
                item.sourceEventId(),
                item.platform(),
                item.chatId(),
                item.senderId(),
                item.title(),
                toTimestamp(item.startTime()),
                toTimestamp(item.endTime()),
                item.location(),
                item.content(),
                item.participants(),
                item.confidence(),
                toTimestamp(item.createdAt())
        );
        return item;
    }

    @Override
    public List<ScheduleItem> findAll() {
        // 这个函数的作用是按开始时间和创建时间稳定排序读取全部日程，保持客户端展示顺序一致。
        return jdbcTemplate.query(
                "SELECT * FROM schedule_item ORDER BY start_time ASC, created_at ASC",
                scheduleItemRowMapper()
        );
    }

    @Override
    public Optional<ScheduleItem> findBySourceEventId(String sourceEventId) {
        // 这个函数的作用是按上游事件 ID 查询日程，用于重复消息消费时直接复用已有记录。
        return jdbcTemplate.query(
                        "SELECT * FROM schedule_item WHERE source_event_id = ?",
                        scheduleItemRowMapper(),
                        sourceEventId
                ).stream()
                .findFirst();
    }

    @Override
    public Optional<ScheduleItem> findById(String id) {
        // 这个函数的作用是按日程主键查询单条记录，供详情和删除前校验使用。
        return jdbcTemplate.query(
                        "SELECT * FROM schedule_item WHERE id = ?",
                        scheduleItemRowMapper(),
                        id
                ).stream()
                .findFirst();
    }

    @Override
    public boolean deleteById(String id) {
        // 这个函数的作用是物理删除指定日程，并通过受影响行数告诉上层记录是否存在。
        return jdbcTemplate.update("DELETE FROM schedule_item WHERE id = ?", id) > 0;
    }

    @Override
    public int deleteExpired(LocalDateTime referenceTime) {
        // 有结束时间时按结束时间判断；没有结束时间时，开始时间就是该事项的逾期边界。
        return jdbcTemplate.update("""
                        DELETE FROM schedule_item
                        WHERE (end_time IS NOT NULL AND end_time <= ?)
                           OR (end_time IS NULL AND start_time <= ?)
                        """,
                toTimestamp(referenceTime),
                toTimestamp(referenceTime)
        );
    }

    private RowMapper<ScheduleItem> scheduleItemRowMapper() {
        // 这个函数的作用是集中定义数据库列到领域模型的映射，避免不同查询产生字段语义偏差。
        return (resultSet, rowNumber) -> new ScheduleItem(
                resultSet.getString("id"),
                resultSet.getString("source_event_id"),
                resultSet.getString("platform"),
                resultSet.getString("chat_id"),
                resultSet.getString("sender_id"),
                resultSet.getString("title"),
                toLocalDateTime(resultSet.getTimestamp("start_time")),
                toLocalDateTime(resultSet.getTimestamp("end_time")),
                resultSet.getString("location"),
                resultSet.getString("content"),
                resultSet.getString("participants"),
                resultSet.getString("confidence"),
                toLocalDateTime(resultSet.getTimestamp("created_at"))
        );
    }

    private static Timestamp toTimestamp(LocalDateTime value) {
        // 这个函数的作用是把可空 LocalDateTime 转为 JDBC Timestamp，正确处理没有结束时间的日程。
        return value == null ? null : Timestamp.valueOf(value);
    }

    private static LocalDateTime toLocalDateTime(Timestamp value) {
        // 这个函数的作用是把数据库可空时间还原为 LocalDateTime，保持 API 原有时间类型不变。
        return value == null ? null : value.toLocalDateTime();
    }
}
