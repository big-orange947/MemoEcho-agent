package com.memoecho.eventcenter.repository;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.memoecho.eventcenter.model.ConversationCognitionCard;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.stereotype.Repository;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Timestamp;
import java.util.List;
import java.util.Optional;

/** 使用参数化 SQL 持久化会话认知卡，兼容 MySQL 与测试环境 H2。 */
@Repository
public class JdbcConversationCognitionCardRepository implements ConversationCognitionCardRepository {

    private final JdbcTemplate jdbcTemplate;
    private final ObjectMapper objectMapper;
    private final RowMapper<ConversationCognitionCard> rowMapper = new CognitionCardRowMapper();

    /** 注入 JDBC 和 JSON 编解码器；结构化字段以 JSON 保存，便于后续演进版本。 */
    public JdbcConversationCognitionCardRepository(JdbcTemplate jdbcTemplate, ObjectMapper objectMapper) {
        this.jdbcTemplate = jdbcTemplate;
        this.objectMapper = objectMapper;
    }

    /** 根据 ID 是否存在选择插入或更新，不依赖数据库专有 UPSERT 语法。 */
    @Override
    public ConversationCognitionCard save(ConversationCognitionCard card) {
        Integer existing = jdbcTemplate.queryForObject(
                "SELECT COUNT(*) FROM conversation_cognition_card WHERE id = ?", Integer.class, card.id());
        if (existing != null && existing > 0) {
            jdbcTemplate.update("""
                            UPDATE conversation_cognition_card SET
                                user_id = ?, platform = ?, chat_type = ?, chat_id = ?, version = ?,
                                relationship_json = ?, preferred_address_json = ?, counterparty_traits_json = ?,
                                owner_expression_habits_json = ?, counterparty_expression_habits_json = ?,
                                background_summary_json = ?, current_progress_json = ?, known_facts_json = ?,
                                recent_topics_json = ?, open_questions_json = ?, source_event_ids_json = ?,
                                source_message_count = ?, status = ?, analyzed_at = ?, updated_at = ?
                            WHERE id = ?
                            """,
                    card.userId(), card.platform(), card.chatType(), card.chatId(), card.version(),
                    writeJson(card.relationship()), writeJson(card.preferredAddress()),
                    writeJson(card.counterpartyTraits()), writeJson(card.ownerExpressionHabits()),
                    writeJson(card.counterpartyExpressionHabits()), writeJson(card.backgroundSummary()),
                    writeJson(card.currentProgress()), writeJson(card.knownFacts()), writeJson(card.recentTopics()),
                    writeJson(card.openQuestions()), writeJson(card.sourceEventIds()), card.sourceMessageCount(),
                    card.status(), Timestamp.from(card.analyzedAt()), Timestamp.from(card.updatedAt()), card.id());
        } else {
            jdbcTemplate.update("""
                            INSERT INTO conversation_cognition_card (
                                id, user_id, platform, chat_type, chat_id, version, relationship_json,
                                preferred_address_json, counterparty_traits_json, owner_expression_habits_json,
                                counterparty_expression_habits_json, background_summary_json, current_progress_json,
                                known_facts_json, recent_topics_json, open_questions_json, source_event_ids_json,
                                source_message_count, status, analyzed_at, created_at, updated_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                    card.id(), card.userId(), card.platform(), card.chatType(), card.chatId(), card.version(),
                    writeJson(card.relationship()), writeJson(card.preferredAddress()),
                    writeJson(card.counterpartyTraits()), writeJson(card.ownerExpressionHabits()),
                    writeJson(card.counterpartyExpressionHabits()), writeJson(card.backgroundSummary()),
                    writeJson(card.currentProgress()), writeJson(card.knownFacts()), writeJson(card.recentTopics()),
                    writeJson(card.openQuestions()), writeJson(card.sourceEventIds()), card.sourceMessageCount(),
                    card.status(), Timestamp.from(card.analyzedAt()), Timestamp.from(card.createdAt()),
                    Timestamp.from(card.updatedAt()));
        }
        return card;
    }

    /** 使用完整作用域读取，避免相同 QQ 号在不同用户或平台之间串卡。 */
    @Override
    public Optional<ConversationCognitionCard> findByScope(
            String userId,
            String platform,
            String chatType,
            String chatId
    ) {
        return jdbcTemplate.query("""
                        SELECT * FROM conversation_cognition_card
                        WHERE user_id = ? AND platform = ? AND chat_type = ? AND chat_id = ?
                        """, rowMapper, userId, platform, chatType, chatId)
                .stream()
                .findFirst();
    }

    /** 删除时同样校验用户和完整会话作用域。 */
    @Override
    public int deleteByScope(String userId, String platform, String chatType, String chatId) {
        return jdbcTemplate.update("""
                DELETE FROM conversation_cognition_card
                WHERE user_id = ? AND platform = ? AND chat_type = ? AND chat_id = ?
                """, userId, platform, chatType, chatId);
    }

    /** 把记录或列表序列化为稳定 JSON。 */
    private String writeJson(Object value) {
        try {
            return objectMapper.writeValueAsString(value);
        } catch (JsonProcessingException exception) {
            throw new IllegalStateException("会话认知卡序列化失败", exception);
        }
    }

    /** 读取单个带来源字段；损坏历史数据降级为空字段，不阻断整张卡。 */
    private ConversationCognitionCard.CognitionField readField(String json) {
        try {
            return objectMapper.readValue(json, ConversationCognitionCard.CognitionField.class);
        } catch (JsonProcessingException | IllegalArgumentException exception) {
            return ConversationCognitionCard.CognitionField.empty();
        }
    }

    /** 读取字符串数组；损坏历史数据降级为空列表。 */
    private List<String> readList(String json) {
        try {
            return objectMapper.readValue(json, new TypeReference<>() { });
        } catch (JsonProcessingException | IllegalArgumentException exception) {
            return List.of();
        }
    }

    /** 把数据库行还原为完整领域对象。 */
    private final class CognitionCardRowMapper implements RowMapper<ConversationCognitionCard> {
        @Override
        public ConversationCognitionCard mapRow(ResultSet rs, int rowNum) throws SQLException {
            return new ConversationCognitionCard(
                    rs.getString("id"), rs.getString("user_id"), rs.getString("platform"),
                    rs.getString("chat_type"), rs.getString("chat_id"), rs.getInt("version"),
                    readField(rs.getString("relationship_json")),
                    readField(rs.getString("preferred_address_json")),
                    readField(rs.getString("counterparty_traits_json")),
                    readField(rs.getString("owner_expression_habits_json")),
                    readField(rs.getString("counterparty_expression_habits_json")),
                    readField(rs.getString("background_summary_json")),
                    readField(rs.getString("current_progress_json")),
                    readList(rs.getString("known_facts_json")), readList(rs.getString("recent_topics_json")),
                    readList(rs.getString("open_questions_json")), readList(rs.getString("source_event_ids_json")),
                    rs.getInt("source_message_count"), rs.getString("status"),
                    rs.getTimestamp("analyzed_at").toInstant(), rs.getTimestamp("created_at").toInstant(),
                    rs.getTimestamp("updated_at").toInstant()
            );
        }
    }
}
