package com.memoecho.eventcenter.service;

import com.memoecho.eventcenter.dto.ConversationCognitionCardResponse;
import com.memoecho.eventcenter.dto.ConversationCognitionCardUpsertRequest;
import com.memoecho.eventcenter.model.ConversationCognitionCard;
import com.memoecho.eventcenter.repository.ConversationCognitionCardRepository;
import org.junit.jupiter.api.Test;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** 验证会话认知卡最重要的字段来源和覆盖保护规则。 */
class ConversationCognitionCardApplicationServiceTest {

    private final InMemoryRepository repository = new InMemoryRepository();
    private final ConversationCognitionCardApplicationService service =
            new ConversationCognitionCardApplicationService(repository);

    /** Runtime 首次分析应生成 AI_INFERRED 字段并保存来源消息。 */
    @Test
    void shouldCreateInferenceCard() {
        ConversationCognitionCardResponse response = service.upsertInference("freeze", request(
                field("同学", 0.82d), field("小号", 0.75d), List.of("event-1")));

        assertEquals("INFERRED", response.status());
        assertEquals("AI_INFERRED", response.relationship().source());
        assertFalse(response.relationship().locked());
        assertEquals(List.of("event-1"), response.sourceEventIds());
    }

    /** 用户覆盖字段后，下一次 Runtime 推断必须保留用户值。 */
    @Test
    void shouldProtectUserOverrideFromRuntimeRefresh() {
        service.upsertInference("freeze", request(
                field("同学", 0.82d), field("小号", 0.75d), List.of("event-1")));
        service.upsertByUser("freeze", request(
                field("熟人", 0.2d), null, null));

        ConversationCognitionCardResponse refreshed = service.upsertInference("freeze", request(
                field("陌生人", 0.99d), field("对方", 0.99d), List.of("event-2")));

        assertEquals("熟人", refreshed.relationship().value());
        assertEquals("USER_OVERRIDE", refreshed.relationship().source());
        assertTrue(refreshed.relationship().locked());
        assertEquals("对方", refreshed.preferredAddress().value());
    }

    /** 用户确认后，已有推断字段应升级为可信且锁定的确认来源。 */
    @Test
    void shouldConfirmExistingFields() {
        service.upsertInference("freeze", request(
                field("同学", 0.82d), field("小号", 0.75d), List.of("event-1")));

        ConversationCognitionCardResponse confirmed = service.confirm(
                "freeze", "qq", "private", "3807050597");

        assertEquals("CONFIRMED", confirmed.status());
        assertEquals("USER_CONFIRMED", confirmed.relationship().source());
        assertTrue(confirmed.relationship().locked());
    }

    /** 构造覆盖主要字段的测试请求。 */
    private ConversationCognitionCardUpsertRequest request(
            ConversationCognitionCard.CognitionField relationship,
            ConversationCognitionCard.CognitionField preferredAddress,
            List<String> sourceEventIds
    ) {
        return new ConversationCognitionCardUpsertRequest(
                "qq", "private", "3807050597", relationship, preferredAddress,
                field("沟通直接", 0.7d), field("短句", 0.9d), field("短句", 0.8d),
                field("正在讨论会员购买", 0.8d), field("等待确认付款方式", 0.8d),
                List.of("对方是同学"), List.of("网易云会员"), List.of("付款方式"), sourceEventIds, 12
        );
    }

    /** 构造 Runtime 风格的推断字段，请求中的来源会被服务端忽略。 */
    private ConversationCognitionCard.CognitionField field(String value, double confidence) {
        return new ConversationCognitionCard.CognitionField(value, "UNTRUSTED", confidence, true);
    }

    /** 单元测试使用的最小内存仓储，键由用户和完整会话作用域组成。 */
    private static final class InMemoryRepository implements ConversationCognitionCardRepository {
        private final Map<String, ConversationCognitionCard> cards = new LinkedHashMap<>();

        /** 保存时按 ID 替换记录。 */
        @Override
        public ConversationCognitionCard save(ConversationCognitionCard card) {
            cards.put(card.id(), card);
            return card;
        }

        /** 按完整作用域查询。 */
        @Override
        public Optional<ConversationCognitionCard> findByScope(
                String userId,
                String platform,
                String chatType,
                String chatId
        ) {
            return cards.values().stream()
                    .filter(card -> card.userId().equals(userId))
                    .filter(card -> card.platform().equals(platform))
                    .filter(card -> card.chatType().equals(chatType))
                    .filter(card -> card.chatId().equals(chatId))
                    .findFirst();
        }

        /** 删除当前作用域的记录。 */
        @Override
        public int deleteByScope(String userId, String platform, String chatType, String chatId) {
            Optional<ConversationCognitionCard> existing = findByScope(userId, platform, chatType, chatId);
            existing.ifPresent(card -> cards.remove(card.id()));
            return existing.isPresent() ? 1 : 0;
        }
    }
}
