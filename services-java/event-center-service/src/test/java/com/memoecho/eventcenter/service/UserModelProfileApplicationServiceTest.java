package com.memoecho.eventcenter.service;

import com.memoecho.eventcenter.config.EventCenterSecurityProperties;
import com.memoecho.eventcenter.dto.UserModelProfileResolveRequest;
import com.memoecho.eventcenter.dto.UserModelProfileResolveResponse;
import com.memoecho.eventcenter.dto.UserModelProfileResponse;
import com.memoecho.eventcenter.dto.UserModelProfileUpsertRequest;
import com.memoecho.eventcenter.repository.InMemoryUserModelProfileRepository;
import org.junit.jupiter.api.Test;
import org.springframework.web.server.ResponseStatusException;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class UserModelProfileApplicationServiceTest {

    private UserModelProfileApplicationService buildService() {
        // 这个函数的作用是为测试统一构造带加密能力的用户模型配置服务。
        EventCenterSecurityProperties properties = new EventCenterSecurityProperties();
        properties.setApiKeySecret("unit-test-secret");
        return new UserModelProfileApplicationService(
                new InMemoryUserModelProfileRepository(),
                new ApiKeyCryptoService(properties)
        );
    }

    @Test
    void shouldResolveRouteSpecificProfileAheadOfDefaultProfile() {
        // 这个测试函数的作用是验证 route 定向模型配置会覆盖同一用户的默认模型配置。
        UserModelProfileApplicationService service = buildService();

        service.createProfile(new UserModelProfileUpsertRequest(
                "freeze",
                "默认聊天模型",
                "用于兜底社交回复",
                true,
                "OPENAI_COMPATIBLE",
                "https://api.openai.com/v1",
                "sk-default-001",
                false,
                "gpt-4o-mini",
                0.7,
                2048,
                List.of(),
                true,
                1
        ));
        UserModelProfileResponse routeProfile = service.createProfile(new UserModelProfileUpsertRequest(
                "freeze",
                "工作任务模型",
                "专门处理任务规划",
                true,
                "OPENAI_COMPATIBLE",
                "https://example.com/v1",
                "sk-work-002",
                false,
                "deepseek-chat",
                0.3,
                4096,
                List.of("task_plan", "work_management"),
                false,
                10
        ));

        UserModelProfileResolveResponse resolved = service.resolveProfile(
                new UserModelProfileResolveRequest("freeze", "task_plan", null)
        );

        assertTrue(resolved.matched());
        assertNotNull(resolved.profile());
        assertEquals(routeProfile.id(), resolved.profile().id());
        assertEquals("deepseek-chat", resolved.profile().model());
        assertEquals("命中 route 定向模型配置", resolved.reason());
    }

    @Test
    void shouldKeepOnlyOneDefaultProfileForSameUser() {
        // 这个测试函数的作用是验证同一用户后创建的默认配置会自动取消旧默认配置。
        UserModelProfileApplicationService service = buildService();

        UserModelProfileResponse first = service.createProfile(new UserModelProfileUpsertRequest(
                "freeze",
                "默认模型一",
                "",
                true,
                null,
                "",
                "sk-one",
                false,
                "gpt-4o-mini",
                null,
                null,
                List.of(),
                true,
                1
        ));
        UserModelProfileResponse second = service.createProfile(new UserModelProfileUpsertRequest(
                "freeze",
                "默认模型二",
                "",
                true,
                null,
                "",
                "sk-two",
                false,
                "gpt-4.1-mini",
                null,
                null,
                List.of(),
                true,
                2
        ));

        UserModelProfileResponse reloadedFirst = service.getProfile(first.id());
        UserModelProfileResponse reloadedSecond = service.getProfile(second.id());

        assertFalse(reloadedFirst.isDefault());
        assertTrue(reloadedSecond.isDefault());
    }

    @Test
    void shouldMaskApiKeyInResponseButExposePlainKeyInResolveResult() {
        // 这个测试函数的作用是验证展示接口只返回脱敏密钥，而运行时解析接口返回明文密钥。
        UserModelProfileApplicationService service = buildService();

        UserModelProfileResponse created = service.createProfile(new UserModelProfileUpsertRequest(
                "freeze",
                "默认模型",
                "",
                true,
                "OPENAI_COMPATIBLE",
                "https://api.openai.com/v1",
                "sk-secret-001",
                false,
                "gpt-4o-mini",
                null,
                null,
                List.of(),
                true,
                1
        ));

        UserModelProfileResponse loaded = service.getProfile(created.id());
        UserModelProfileResolveResponse resolved = service.resolveProfile(
                new UserModelProfileResolveRequest("freeze", "social_reply", null)
        );

        assertTrue(loaded.hasApiKey());
        assertTrue(loaded.apiKeyMasked().contains("****"));
        assertEquals("sk-secret-001", resolved.profile().apiKey());
    }

    @Test
    void shouldResolveExplicitProfileAheadOfRouteDefaults() {
        // 这个测试函数的作用是验证当会话显式绑定 modelProfileId 时，运行时会优先命中这条模型配置。
        UserModelProfileApplicationService service = buildService();

        service.createProfile(new UserModelProfileUpsertRequest(
                "freeze",
                "默认模型",
                "",
                true,
                "OPENAI_COMPATIBLE",
                "https://api.openai.com/v1",
                "sk-default-001",
                false,
                "gpt-4o-mini",
                0.7,
                2048,
                List.of("social_reply"),
                true,
                1
        ));
        UserModelProfileResponse explicit = service.createProfile(new UserModelProfileUpsertRequest(
                "freeze",
                "会话专属模型",
                "",
                true,
                "OPENAI_COMPATIBLE",
                "https://example.com/v1",
                "sk-explicit-001",
                false,
                "deepseek-chat",
                0.3,
                4096,
                List.of("task_plan"),
                false,
                50
        ));

        UserModelProfileResolveResponse resolved = service.resolveProfile(
                new UserModelProfileResolveRequest("freeze", "social_reply", explicit.id())
        );

        assertTrue(resolved.matched());
        assertNotNull(resolved.profile());
        assertEquals(explicit.id(), resolved.profile().id());
        assertEquals("命中会话显式绑定模型配置", resolved.reason());
        assertEquals("deepseek-chat", resolved.profile().model());
    }

    /**
     * 验证用户范围方法忽略请求体 userId，且不会读取、更新或删除其他用户的模型配置。
     */
    @Test
    void shouldIsolateModelProfilesBetweenUsers() {
        UserModelProfileApplicationService service = buildService();

        UserModelProfileResponse created = service.createProfile("freeze", new UserModelProfileUpsertRequest(
                "another-user", "个人社交模型", "", true, "OPENAI_COMPATIBLE",
                "https://example.com/v1", "sk-freeze", false, "gpt-4o-mini", null,
                null, List.of("social_reply"), true, 1
        ));

        assertEquals("freeze", created.userId());
        assertEquals(1, service.listProfiles("freeze").size());
        assertTrue(service.listProfiles("another-user").isEmpty());
        assertFalse(service.resolveProfile("another-user", new UserModelProfileResolveRequest(
                "freeze", "social_reply", created.id())).matched());
        assertThrows(ResponseStatusException.class, () -> service.updateProfile(
                "another-user", created.id(), new UserModelProfileUpsertRequest(
                        "another-user", "越权更新", "", true, null, "", null, false,
                        "gpt-4.1-mini", null, null, List.of(), false, 1
                )));
        assertThrows(ResponseStatusException.class, () -> service.deleteProfile("another-user", created.id()));
        assertEquals("freeze", service.getProfile("freeze", created.id()).userId());
    }
}
