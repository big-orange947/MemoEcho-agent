package com.memoecho.eventcenter.repository;

import com.memoecho.eventcenter.model.ConversationProfile;
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
        ConversationProfile profile = profile(
                "profile-001",
                "freeze",
                "私聊人格",
                List.of("123456", "789012"),
                List.of("https://github.com/example/skill,with-comma", "local://skills/reply"),
                30
        );

        repository.save(profile);
        ConversationProfile reloaded = repository.findById("profile-001").orElseThrow();

        assertEquals("freeze", reloaded.userId());
        assertEquals(List.of("123456", "789012"), reloaded.chatIds());
        assertEquals(List.of("提醒,重要", "deadline"), reloaded.triggerKeywords());
        assertEquals(profile.skillReferences(), reloaded.skillReferences());
        assertEquals(List.of("send_message", "create_schedule"), reloaded.allowedTools());
        assertEquals(120, reloaded.digestWindowSeconds());
        assertTrue(reloaded.includeUrgentInDigest());
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
