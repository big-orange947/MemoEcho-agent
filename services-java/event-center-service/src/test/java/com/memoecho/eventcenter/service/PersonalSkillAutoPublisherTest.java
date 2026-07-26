package com.memoecho.eventcenter.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.memoecho.eventcenter.config.SkillStoreProperties;
import com.memoecho.eventcenter.dto.SenderPayload;
import com.memoecho.eventcenter.dto.UnifiedEventPayload;
import com.memoecho.eventcenter.model.ConversationProfile;
import com.memoecho.eventcenter.model.StoredEvent;
import com.memoecho.eventcenter.repository.InMemoryEventRecordRepository;
import com.memoecho.eventcenter.repository.ConversationProfileRepository;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.List;
import java.util.stream.Stream;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class PersonalSkillAutoPublisherTest {

    @TempDir
    Path tempDir;

    @Test
    void shouldPublishExplainableVersionedSkillWithoutRawMessages() throws Exception {
        // 该测试验证成熟样本会生成可加载描述符、版本快照和不含原文的说明文件。
        InMemoryEventRecordRepository eventRepository = new InMemoryEventRecordRepository();
        Instant start = Instant.parse("2026-01-01T00:00:00Z");
        for (int index = 0; index < 100; index++) {
            String text = index % 2 == 0 ? "行啊" + index : "晚点说" + index + "？";
            eventRepository.save(event("sample-" + index, text, start.plus(index * 9L, ChronoUnit.HOURS)));
        }

        ConversationProfile profile = mock(ConversationProfile.class);
        when(profile.id()).thenReturn("friends-style");
        when(profile.userId()).thenReturn("user-1");
        when(profile.name()).thenReturn("朋友聊天");
        when(profile.platform()).thenReturn("qq");
        when(profile.chatType()).thenReturn("private");
        when(profile.chatIds()).thenReturn(List.of("chat-1"));
        ConversationProfileRepository profileRepository = mock(ConversationProfileRepository.class);
        when(profileRepository.findAll()).thenReturn(List.of(profile));

        SkillStoreProperties properties = new SkillStoreProperties();
        properties.setInstalledRoot(tempDir.toString());
        ObjectMapper objectMapper = new ObjectMapper();
        PersonalSkillAutoPublisher publisher = new PersonalSkillAutoPublisher(
                profileRepository,
                eventRepository,
                properties,
                objectMapper,
                new PersonalStyleAnalyzer(),
                100,
                0.75,
                30
        );

        PersonalSkillAutoPublisher.PublicationResult result = publisher.evaluate(profile);

        assertTrue(result.published());
        Path skillDirectory = tempDir.resolve("personal/user-1/friends-style");
        Path descriptorPath = skillDirectory.resolve("skill.json");
        assertTrue(Files.exists(descriptorPath));
        assertTrue(Files.exists(skillDirectory.resolve("SKILL.md")));
        JsonNode descriptor = objectMapper.readTree(Files.readString(descriptorPath));
        assertEquals("AVAILABLE", descriptor.path("maturity").path("status").asText());
        assertEquals(100, descriptor.path("maturity").path("sampleCount").asInt());
        assertTrue(descriptor.path("trainingPolicy").path("selfAuthoredOnly").asBoolean());
        assertFalse(descriptor.path("trainingPolicy").path("rawMessagesEmbedded").asBoolean());
        assertEquals("PROFILE_OVER_MODE_OVER_GLOBAL", descriptor.path("styleHierarchy").path("strategy").asText());
        assertEquals(3, descriptor.path("styleHierarchy").path("layers").size());
        assertEquals(100, descriptor.path("styleHierarchy").path("layers").get(0).path("sampleCount").asInt());
        assertTrue(descriptor.path("promptFragments").path("system").asText().contains("只模仿表达形式"));
        assertFalse(Files.readString(descriptorPath).contains("晚点说99"));
        assertEquals(1, countVersionFiles(skillDirectory.resolve("versions")));

        publisher.evaluate(profile);
        assertEquals(1, countVersionFiles(skillDirectory.resolve("versions")));
    }

    @Test
    void shouldBuildGlobalModeAndProfileLayersFromAuthorizedScopesOnly() throws Exception {
        // 该测试验证全局层可跨私聊和群聊聚合，模式层只聚合同类会话，当前设定层只读取自身会话。
        InMemoryEventRecordRepository eventRepository = new InMemoryEventRecordRepository();
        Instant start = Instant.parse("2026-02-01T00:00:00Z");
        for (int index = 0; index < 100; index++) {
            Instant timestamp = start.plus(index * 10L, ChronoUnit.HOURS);
            eventRepository.save(event("current-" + index, "当前私聊" + index, timestamp, "chat-current", "private"));
            eventRepository.save(event("other-" + index, "其他私聊" + index, timestamp, "chat-other", "private"));
            eventRepository.save(event("group-" + index, "群聊表达" + index, timestamp, "chat-group", "group"));
            eventRepository.save(event("blocked-" + index, "未授权会话" + index, timestamp, "chat-blocked", "private"));
        }

        ConversationProfile current = profile("current", "当前朋友", "private", "chat-current", true);
        ConversationProfile otherPrivate = profile("other", "其他朋友", "private", "chat-other", true);
        ConversationProfile group = profile("group", "同学群", "group", "chat-group", true);
        ConversationProfile blocked = profile("blocked", "未授权", "private", "chat-blocked", false);
        ConversationProfileRepository profileRepository = mock(ConversationProfileRepository.class);
        when(profileRepository.findAll()).thenReturn(List.of(current, otherPrivate, group, blocked));

        SkillStoreProperties properties = new SkillStoreProperties();
        properties.setInstalledRoot(tempDir.toString());
        ObjectMapper objectMapper = new ObjectMapper();
        PersonalSkillAutoPublisher publisher = new PersonalSkillAutoPublisher(
                profileRepository,
                eventRepository,
                properties,
                objectMapper,
                new PersonalStyleAnalyzer(),
                100,
                0.75,
                30
        );

        PersonalSkillAutoPublisher.PublicationResult result = publisher.evaluate(current);
        JsonNode layers = objectMapper.readTree(Files.readString(
                tempDir.resolve("personal/user-1/current/skill.json")
        )).path("styleHierarchy").path("layers");

        assertTrue(result.published());
        assertEquals(300, layers.get(0).path("sampleCount").asInt());
        assertEquals(200, layers.get(1).path("sampleCount").asInt());
        assertEquals(100, layers.get(2).path("sampleCount").asInt());
    }

    /**
     * 统计个人 Skill 的不可变版本快照，并确保目录流在 Windows 上及时关闭。
     */
    private long countVersionFiles(Path versionsDirectory) throws Exception {
        try (Stream<Path> paths = Files.list(versionsDirectory)) {
            return paths.count();
        }
    }

    /**
     * 构造由本人手动发送的训练事件。
     */
    private StoredEvent event(String id, String text, Instant timestamp) {
        return event(id, text, timestamp, "chat-1", "private");
    }

    /**
     * 构造指定会话范围内由本人发送的训练事件，用于验证分层样本隔离。
     */
    private StoredEvent event(String id, String text, Instant timestamp, String chatId, String chatType) {
        UnifiedEventPayload payload = new UnifiedEventPayload(
                id,
                "qq",
                "life",
                "message",
                chatType,
                chatId,
                "self",
                new SenderPayload("self", "freeze", null),
                text,
                List.of(),
                List.of(),
                timestamp.toString(),
                null
        );
        return StoredEvent.received(id, "user-1", payload, timestamp).withMessageOrigin("USER_MANUAL");
    }

    /**
     * 构造训练授权可控的会话设定，减少测试对完整数据模型构造函数的依赖。
     */
    private ConversationProfile profile(
            String id,
            String name,
            String chatType,
            String chatId,
            boolean trainingEnabled
    ) {
        ConversationProfile profile = mock(ConversationProfile.class);
        when(profile.id()).thenReturn(id);
        when(profile.userId()).thenReturn("user-1");
        when(profile.name()).thenReturn(name);
        when(profile.platform()).thenReturn("qq");
        when(profile.chatType()).thenReturn(chatType);
        when(profile.chatIds()).thenReturn(List.of(chatId));
        when(profile.enabled()).thenReturn(true);
        when(profile.historyTrainingEnabled()).thenReturn(trainingEnabled);
        return profile;
    }
}
