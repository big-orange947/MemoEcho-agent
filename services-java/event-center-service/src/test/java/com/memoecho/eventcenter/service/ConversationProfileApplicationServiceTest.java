package com.memoecho.eventcenter.service;

import com.memoecho.eventcenter.dto.ConversationProfileMatchRequest;
import com.memoecho.eventcenter.dto.ConversationProfileMatchResponse;
import com.memoecho.eventcenter.dto.ConversationProfileResponse;
import com.memoecho.eventcenter.dto.ConversationProfileUpsertRequest;
import com.memoecho.eventcenter.repository.InMemoryConversationProfileRepository;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

class ConversationProfileApplicationServiceTest {

    @Test
    void shouldNormalizeAndExposeNotificationPolicy() {
        // 这个测试函数的作用是验证会话设定集能够保存通知策略，并对不合理摘要参数施加安全边界。
        ConversationProfileApplicationService service = new ConversationProfileApplicationService(
                new InMemoryConversationProfileRepository()
        );

        ConversationProfileResponse response = service.createProfile(new ConversationProfileUpsertRequest(
                "项目群摘要模式", "", true, "qq", "", "work", "group", List.of("1098307542"),
                List.of(), List.of(), "ALWAYS", List.of(), "NONE", "", "", List.of(), "", "",
                "SILENT", null, null, List.of(), false, 1,
                "digest_only", List.of("截止", "发布"), 10, 500, true
        ));

        assertEquals("DIGEST_ONLY", response.notificationMode());
        assertEquals(List.of("截止", "发布"), response.notificationKeywords());
        assertEquals(60, response.digestWindowSeconds());
        assertEquals(100, response.digestMaxMessages());
        assertTrue(response.includeUrgentInDigest());
    }

    @Test
    void shouldMatchHighestPriorityActiveProfile() {
        // 这个测试函数的作用是验证同一会话存在多条设定时，系统会按优先级和具体度选出真正生效的那条。
        ConversationProfileApplicationService service = new ConversationProfileApplicationService(
                new InMemoryConversationProfileRepository()
        );

        service.createProfile(new ConversationProfileUpsertRequest(
                "默认私聊设定",
                "用于一般私聊",
                true,
                "qq",
                "3969785168",
                "life",
                "private",
                List.of("2597164807"),
                List.of(),
                List.of("social_reply"),
                "ALWAYS",
                List.of(),
                "PROMPT",
                "默认私聊人设",
                "",
                List.of(),
                "",
                "social_reply",
                "DRAFT_ONLY",
                null,
                null,
                List.of(),
                true,
                1
        ));
        ConversationProfileResponse highPriorityProfile = service.createProfile(new ConversationProfileUpsertRequest(
                "重要联系人自动回",
                "用于展示高优先级覆盖",
                true,
                "qq",
                "3969785168",
                "life",
                "private",
                List.of("2597164807"),
                List.of("10001"),
                List.of("social_reply"),
                "KEYWORD_ONLY",
                List.of("紧急", "马上"),
                "SKILL",
                "高优先级联系人 prompt",
                "github://demo/skill",
                List.of("github://demo/skill", "github://demo/skill-2"),
                "model-profile-001",
                "social_reply",
                "AUTO_REPLY",
                3,
                5,
                List.of("send_qq_message"),
                false,
                10
        ));

        ConversationProfileMatchResponse response = service.matchProfile(new ConversationProfileMatchRequest(
                "qq",
                "3969785168",
                "life",
                "private",
                "2597164807",
                "10001",
                null,
                "social_reply",
                "这件事很紧急，麻烦马上处理",
                false
        ));

        assertTrue(response.matched());
        assertTrue(response.active());
        assertNotNull(response.profile());
        assertEquals(highPriorityProfile.id(), response.profile().id());
        assertEquals("AUTO_REPLY", response.profile().replyMode());
        assertEquals("social_reply", response.profile().preferredRoute());
        assertEquals("3969785168", response.profile().accountId());
        assertEquals(List.of("github://demo/skill", "github://demo/skill-2"), response.profile().skillReferences());
        assertEquals("model-profile-001", response.profile().modelProfileId());
    }

    @Test
    void shouldReturnMatchedButInactiveWhenTriggerConditionFails() {
        // 这个测试函数的作用是验证命中会话范围但不满足触发条件时，会返回 matched=true、active=false。
        ConversationProfileApplicationService service = new ConversationProfileApplicationService(
                new InMemoryConversationProfileRepository()
        );

        service.createProfile(new ConversationProfileUpsertRequest(
                "仅 at 生效",
                "群里只在被 at 时自动回复",
                true,
                "qq",
                "3969785168",
                "work",
                "group",
                List.of("1098307542"),
                List.of(),
                List.of("schedule_extract"),
                "AT_SELF_ONLY",
                List.of(),
                "PROMPT",
                "会议助手 prompt",
                "",
                List.of(),
                "",
                "schedule_extract",
                "AUTO_REPLY",
                null,
                null,
                List.of(),
                false,
                5
        ));

        ConversationProfileMatchResponse response = service.matchProfile(new ConversationProfileMatchRequest(
                "qq",
                "3969785168",
                "work",
                "group",
                "1098307542",
                "2597164807",
                "owner",
                "schedule_extract",
                "今天下午两点开会",
                false
        ));

        assertTrue(response.matched());
        assertFalse(response.active());
        assertEquals("命中会话范围，但当前消息未满足触发条件", response.reason());
        assertNotNull(response.profile());
        assertEquals("schedule_extract", response.profile().preferredRoute());
    }

    @Test
    void shouldPreferRouteSpecificProfileWhenSameChatHasDifferentSkills() {
        // 这个测试函数的作用是验证同一会话可以按不同 route 命中不同 skill 设定。
        ConversationProfileApplicationService service = new ConversationProfileApplicationService(
                new InMemoryConversationProfileRepository()
        );

        service.createProfile(new ConversationProfileUpsertRequest(
                "同会话社交回复设定",
                "",
                true,
                "qq",
                "3969785168",
                "life",
                "private",
                List.of("2597164807"),
                List.of("10001"),
                List.of("social_reply"),
                "ALWAYS",
                List.of(),
                "PROMPT",
                "社交风格",
                "",
                List.of("skill://social"),
                "",
                "social_reply",
                "DRAFT_ONLY",
                null,
                null,
                List.of(),
                false,
                5
        ));
        service.createProfile(new ConversationProfileUpsertRequest(
                "同会话任务规划设定",
                "",
                true,
                "qq",
                "3969785168",
                "life",
                "private",
                List.of("2597164807"),
                List.of("10001"),
                List.of("task_plan"),
                "ALWAYS",
                List.of(),
                "PROMPT",
                "任务规划风格",
                "",
                List.of("skill://task"),
                "",
                "task_plan",
                "AUTO_REPLY",
                null,
                null,
                List.of("create_task"),
                false,
                5
        ));

        ConversationProfileMatchResponse response = service.matchProfile(new ConversationProfileMatchRequest(
                "qq",
                "3969785168",
                "life",
                "private",
                "2597164807",
                "10001",
                null,
                "task_plan",
                "我今天该做什么",
                false
        ));

        assertTrue(response.matched());
        assertTrue(response.active());
        assertEquals(List.of("skill://task"), response.profile().skillReferences());
        assertEquals(List.of("create_task"), response.profile().allowedTools());
        assertEquals("task_plan", response.profile().preferredRoute());
    }
}
