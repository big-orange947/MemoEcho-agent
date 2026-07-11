package com.memoecho.eventcenter.controller;

import com.memoecho.eventcenter.dto.UserModelProfileResolveRequest;
import com.memoecho.eventcenter.dto.UserModelProfileResolveResponse;
import com.memoecho.eventcenter.dto.UserModelProfileResolvedResponse;
import com.memoecho.eventcenter.dto.UserModelProfileResponse;
import com.memoecho.eventcenter.dto.UserModelProfileUpsertRequest;
import com.memoecho.eventcenter.service.LocalUserContextResolver;
import com.memoecho.eventcenter.service.UserModelProfileApplicationService;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;

import java.time.Instant;
import java.util.List;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.BDDMockito.given;
import static org.mockito.Mockito.verify;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@WebMvcTest(InternalUserModelProfileController.class)
class InternalUserModelProfileControllerTest {

    @Autowired
    private MockMvc mockMvc;

    @MockBean
    private UserModelProfileApplicationService applicationService;

    @MockBean
    private LocalUserContextResolver userContextResolver;

    /**
     * 为控制器测试模拟已认证用户，确保所有调用都落在同一个用户边界中。
     */
    private void mockCurrentUser() {
        given(userContextResolver.resolve(any(), any())).willReturn("freeze");
    }

    /**
     * 验证列表接口不会调用跨用户的全量查询服务。
     */
    @Test
    void shouldListProfilesForCurrentUser() throws Exception {
        mockCurrentUser();
        given(applicationService.listProfiles("freeze")).willReturn(List.of(profileResponse()));

        mockMvc.perform(get("/internal/user-model-profiles"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$[0].userId").value("freeze"))
                .andExpect(jsonPath("$[0].model").value("gpt-4o-mini"));

        verify(applicationService).listProfiles("freeze");
    }

    /**
     * 验证创建接口以认证用户为归属，而非相信请求体传入的 userId。
     */
    @Test
    void shouldCreateProfileForAuthenticatedUser() throws Exception {
        mockCurrentUser();
        given(applicationService.createProfile(eq("freeze"), any(UserModelProfileUpsertRequest.class)))
                .willReturn(profileResponse());

        mockMvc.perform(post("/internal/user-model-profiles")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {
                                  "userId": "another-user",
                                  "name": "默认社交模型",
                                  "provider": "OPENAI_COMPATIBLE",
                                  "model": "gpt-4o-mini"
                                }
                                """))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.name").value("默认社交模型"));

        verify(applicationService).createProfile(eq("freeze"), any(UserModelProfileUpsertRequest.class));
    }

    /**
     * 验证携带伪造 userId 的解析请求仍会使用认证用户范围。
     */
    @Test
    void shouldResolveProfileForAuthenticatedUser() throws Exception {
        mockCurrentUser();
        given(applicationService.resolveProfile(eq("freeze"), any(UserModelProfileResolveRequest.class)))
                .willReturn(new UserModelProfileResolveResponse(
                        true,
                        "命中 route 定向模型配置",
                        new UserModelProfileResolvedResponse(
                                "model-profile-001", "freeze", "工作任务模型", "OPENAI_COMPATIBLE",
                                "https://example.com/v1", "sk-route-001", "deepseek-chat", 0.3,
                                4096, List.of("task_plan"), false, 10
                        )
                ));

        mockMvc.perform(post("/internal/user-model-profiles/resolve")
                        .header("Authorization", "Bearer demo-token")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"userId":"another-user","route":"task_plan","profileId":"model-profile-001"}
                                """))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.matched").value(true))
                .andExpect(jsonPath("$.profile.model").value("deepseek-chat"));

        verify(applicationService).resolveProfile(eq("freeze"), any(UserModelProfileResolveRequest.class));
    }

    /**
     * 验证没有用户 JWT 的 Python runtime 可以通过独立服务令牌解析自己的模型配置。
     */
    @Test
    void shouldResolveProfileForRuntimeServiceToken() throws Exception {
        given(userContextResolver.resolveRuntimeUser("runtime-token", "freeze")).willReturn("freeze");
        given(applicationService.resolveProfile(eq("freeze"), any(UserModelProfileResolveRequest.class)))
                .willReturn(new UserModelProfileResolveResponse(false, "未命中用户模型配置", null));

        mockMvc.perform(post("/internal/user-model-profiles/resolve")
                        .header("X-Memo-Echo-Runtime-Token", "runtime-token")
                        .header("X-Memo-Echo-User-Id", "freeze")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"route\":\"social_reply\"}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.matched").value(false));

        verify(userContextResolver).resolveRuntimeUser("runtime-token", "freeze");
        verify(applicationService).resolveProfile(eq("freeze"), any(UserModelProfileResolveRequest.class));
    }

    /**
     * 验证删除接口把当前认证用户传递给服务层进行所有权校验。
     */
    @Test
    void shouldDeleteProfileForCurrentUser() throws Exception {
        mockCurrentUser();

        mockMvc.perform(delete("/internal/user-model-profiles/model-profile-001"))
                .andExpect(status().isNoContent());

        verify(applicationService).deleteProfile("freeze", "model-profile-001");
    }

    /**
     * 验证更新接口不会使用请求体 userId 来改变配置归属。
     */
    @Test
    void shouldUpdateProfileForCurrentUser() throws Exception {
        mockCurrentUser();
        given(applicationService.updateProfile(eq("freeze"), eq("model-profile-001"), any(UserModelProfileUpsertRequest.class)))
                .willReturn(profileResponse());

        mockMvc.perform(put("/internal/user-model-profiles/model-profile-001")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"userId":"another-user","name":"默认社交模型","model":"gpt-4.1-mini"}
                                """))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.id").value("model-profile-001"));

        verify(applicationService).updateProfile(eq("freeze"), eq("model-profile-001"), any(UserModelProfileUpsertRequest.class));
    }

    /**
     * 构造脱敏后的模型配置响应，供控制器 JSON 序列化测试使用。
     */
    private UserModelProfileResponse profileResponse() {
        return new UserModelProfileResponse(
                "model-profile-001", "freeze", "默认社交模型", "用于私聊回复", true,
                "OPENAI_COMPATIBLE", "https://api.openai.com/v1", true, "sk-d****0001",
                "gpt-4o-mini", 0.7, 2048, List.of("social_reply"), true, 8,
                Instant.parse("2026-07-09T00:00:00Z"), Instant.parse("2026-07-09T00:10:00Z")
        );
    }
}
