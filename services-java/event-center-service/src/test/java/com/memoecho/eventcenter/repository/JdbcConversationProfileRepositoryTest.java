package com.memoecho.eventcenter.repository;

import com.memoecho.eventcenter.model.ConversationProfile;
import com.memoecho.eventcenter.model.ConversationProfileContext;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.jdbc.JdbcTest;
import org.springframework.context.annotation.Import;
import org.springframework.test.context.TestPropertySource;

import java.time.Instant;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

@JdbcTest
@Import(JdbcConversationProfileRepository.class)
@TestPropertySource(properties = "spring.sql.init.mode=always")
class JdbcConversationProfileRepositoryTest {

    @Autowired
    private JdbcConversationProfileRepository repository;

    /**
     * 验证完整设定集写入数据库后，所有标量和 JSON 列表字段都能无损读取。
     */
    @Test
    void shouldSaveAndLoadCompleteConversationProfile() {
        ConversationProfileContext profileContext = new ConversationProfileContext(
                2,
                new ConversationProfileContext.Identity(
                        "freeze", "网易云会员卖家", "简短自然", List.of("客服腔", "虚构承诺")
                ),
                new ConversationProfileContext.Counterparty(
                        "小号", "潜在买家", "首次交易", "你", List.of("需要一个月会员"), "MEDIUM", "短句沟通"
                ),
                new ConversationProfileContext.Background(
                        "对方询问会员价格", "已告知月卡 15 元", "等待确认购买"
                ),
                new ConversationProfileContext.Task(
                        "完成真实交易", List.of("确认套餐", "确认付款"), "2026-07-20T20:00:00+08:00",
                        List.of("不得编造收款信息")
                ),
                new ConversationProfileContext.BusinessRules(
                        "月卡 15 元，年卡 50 元", "15", "未交付可退款", "确认到账后交付",
                        List.of("不得低于最低价")
                ),
                new ConversationProfileContext.MemoryPolicy(true),
                List.of(new ConversationProfileContext.AssetReference(
                        "asset-payment-001", "PAYMENT_QR", "微信收款码", "当前账号的收款码", "买家确认购买后"
                ))
        );
        ConversationProfile profile = profile(
                "profile-001",
                "freeze",
                "私聊人格",
                List.of("123456", "789012"),
                List.of("https://github.com/example/skill,with-comma", "local://skills/reply"),
                30
        ).withProfileContext(profileContext);

        repository.save(profile);
        ConversationProfile reloaded = repository.findById("profile-001").orElseThrow();

        assertEquals("freeze", reloaded.userId());
        assertEquals(List.of("123456", "789012"), reloaded.chatIds());
        assertEquals(List.of("提醒,重要", "deadline"), reloaded.triggerKeywords());
        assertEquals(profile.skillReferences(), reloaded.skillReferences());
        assertEquals(List.of("send_message", "create_schedule"), reloaded.allowedTools());
        assertEquals(120, reloaded.digestWindowSeconds());
        assertTrue(reloaded.includeUrgentInDigest());
        assertEquals("freeze", reloaded.profileContext().identity().representedPerson());
        assertEquals("完成真实交易", reloaded.profileContext().task().objective());
        assertEquals("15", reloaded.profileContext().businessRules().minimumPrice());
        assertEquals("asset-payment-001", reloaded.profileContext().assets().getFirst().assetId());
    }

    /**
     * 验证相同主键再次保存时会覆盖旧值，而不是产生重复记录。
     */
    @Test
    void shouldUpdateExistingProfileWithMerge() {
        repository.save(profile("profile-002", "freeze", "更新前", List.of("10001"), List.of(), 10));
        repository.save(profile("profile-002", "freeze", "更新后", List.of("10002"), List.of("builtin://concise"), 50));

        ConversationProfile reloaded = repository.findById("profile-002").orElseThrow();

        assertEquals("更新后", reloaded.name());
        assertEquals(List.of("10002"), reloaded.chatIds());
        assertEquals(50, reloaded.priority());
        assertEquals(1, repository.findAll().size());
    }

    /**
     * 验证用户维度查询和删除不会读取或修改其他用户的设定集。
     */
    @Test
    void shouldIsolateProfilesByOwner() {
        repository.save(profile("profile-freeze", "freeze", "Freeze 设定", List.of("10001"), List.of(), 20));
        repository.save(profile("profile-alice", "alice", "Alice 设定", List.of("10002"), List.of(), 20));

        assertEquals(1, repository.findAllByUserId("freeze").size());
        assertTrue(repository.findByIdAndUserId("profile-freeze", "freeze").isPresent());
        assertFalse(repository.findByIdAndUserId("profile-alice", "freeze").isPresent());

        repository.deleteByIdAndUserId("profile-alice", "freeze");
        assertTrue(repository.findById("profile-alice").isPresent());

        repository.deleteByIdAndUserId("profile-freeze", "freeze");
        assertFalse(repository.findById("profile-freeze").isPresent());
    }

    /**
     * 构造覆盖人格、Skill、回复策略和通知策略的测试设定集。
     */
    private static ConversationProfile profile(
            String id,
            String userId,
            String name,
            List<String> chatIds,
            List<String> skillReferences,
            int priority
    ) {
        return new ConversationProfile(
                id,
                userId,
                name,
                "用于 JDBC 集成测试",
                true,
                "qq",
                "3969785168",
                "life",
                "private",
                chatIds,
                List.of("2597164807"),
                List.of("social_reply", "chat_summary"),
                "AT_SELF_OR_KEYWORD",
                List.of("提醒,重要", "deadline"),
                "SKILL_AND_PROMPT",
                "请使用简洁、自然的语气回复。",
                skillReferences.isEmpty() ? "" : skillReferences.getFirst(),
                skillReferences,
                "model-profile-001",
                "social_reply",
                "DRAFT_ONLY",
                2,
                8,
                List.of("send_message", "create_schedule"),
                true,
                priority,
                Instant.parse("2026-07-11T00:00:00Z"),
                Instant.parse("2026-07-11T01:00:00Z"),
                "DIGEST",
                List.of("通知", "截止"),
                120,
                20,
                true
        );
    }
}
