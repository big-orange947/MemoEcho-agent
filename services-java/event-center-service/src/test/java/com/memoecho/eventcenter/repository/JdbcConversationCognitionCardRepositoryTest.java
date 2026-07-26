package com.memoecho.eventcenter.repository;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;
import com.memoecho.eventcenter.model.ConversationCognitionCard;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.jdbc.JdbcTest;
import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Import;
import org.springframework.test.context.TestPropertySource;

import java.time.Instant;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** 验证会话认知卡仓储的 JSON 映射、更新和用户隔离。 */
@JdbcTest
@Import({JdbcConversationCognitionCardRepository.class, JdbcConversationCognitionCardRepositoryTest.JsonConfig.class})
@TestPropertySource(properties = "spring.sql.init.mode=always")
class JdbcConversationCognitionCardRepositoryTest {

    @Autowired
    private JdbcConversationCognitionCardRepository repository;

    /** 保存后应按完整会话作用域还原字段来源、列表和时间。 */
    @Test
    void shouldRoundTripCognitionCard() {
        repository.save(card("card-1", "freeze", "同学", "AI_INFERRED", false));

        ConversationCognitionCard loaded = repository.findByScope(
                "freeze", "qq", "private", "3807050597").orElseThrow();

        assertEquals("同学", loaded.relationship().value());
        assertEquals("AI_INFERRED", loaded.relationship().source());
        assertEquals(List.of("对方正在咨询会员"), loaded.recentTopics());
        assertEquals(List.of("event-1", "event-2"), loaded.sourceEventIds());
    }

    /** 同 ID 更新应保留唯一记录并写入新字段，不能插入第二行。 */
    @Test
    void shouldUpdateExistingCardById() {
        repository.save(card("card-1", "freeze", "同学", "AI_INFERRED", false));
        repository.save(card("card-1", "freeze", "朋友", "USER_OVERRIDE", true));

        ConversationCognitionCard loaded = repository.findByScope(
                "freeze", "qq", "private", "3807050597").orElseThrow();

        assertEquals("朋友", loaded.relationship().value());
        assertTrue(loaded.relationship().locked());
    }

    /** 相同平台和 chatId 也不能跨用户读取。 */
    @Test
    void shouldEnforceOwnershipBoundary() {
        repository.save(card("card-1", "freeze", "同学", "AI_INFERRED", false));

        assertTrue(repository.findByScope("another-user", "qq", "private", "3807050597").isEmpty());
    }

    /** 构造一张包含字段来源和证据游标的测试认知卡。 */
    private ConversationCognitionCard card(
            String id,
            String userId,
            String relationship,
            String source,
            boolean locked
    ) {
        Instant now = Instant.parse("2026-07-20T02:00:00Z");
        ConversationCognitionCard.CognitionField empty = ConversationCognitionCard.CognitionField.empty();
        return new ConversationCognitionCard(
                id, userId, "qq", "private", "3807050597", 1,
                new ConversationCognitionCard.CognitionField(relationship, source, 0.88d, locked),
                empty, empty, empty, empty, empty, empty,
                List.of("对方昵称是小号"), List.of("对方正在咨询会员"), List.of("付款方式尚未确认"),
                List.of("event-1", "event-2"), 18, "INFERRED", now, now, now
        );
    }

    /** 为纯 JDBC 测试提供和生产一致的 JSON 能力。 */
    @TestConfiguration
    static class JsonConfig {
        /** 创建支持 Java 时间类型的 ObjectMapper。 */
        @Bean
        ObjectMapper objectMapper() {
            return new ObjectMapper().registerModule(new JavaTimeModule());
        }
    }
}
