package com.memoecho.eventcenter.controller;

import com.memoecho.eventcenter.dto.ConversationProfileConfigurationResponse;
import com.memoecho.eventcenter.dto.ConversationProfileMatchRequest;
import com.memoecho.eventcenter.dto.ConversationProfileMatchResponse;
import com.memoecho.eventcenter.dto.ConversationProfileResponse;
import com.memoecho.eventcenter.dto.ConversationProfileUpsertRequest;
import com.memoecho.eventcenter.dto.SkillDescriptorResponse;
import com.memoecho.eventcenter.dto.SkillModelHintsResponse;
import com.memoecho.eventcenter.dto.SkillPromptFragmentsResponse;
import com.memoecho.eventcenter.dto.SkillToolPolicyResponse;
import com.memoecho.eventcenter.service.ConversationProfileApplicationService;
import com.memoecho.eventcenter.service.LocalUserContextResolver;
import com.memoecho.eventcenter.service.SkillCatalogApplicationService;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.BeforeEach;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;

import java.time.Instant;
import java.util.List;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.ArgumentMatchers.nullable;
import static org.mockito.BDDMockito.given;
import static org.mockito.Mockito.verify;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@WebMvcTest(InternalConversationProfileController.class)
class InternalConversationProfileControllerTest {

    @Autowired
    private MockMvc mockMvc;

    @MockBean
    private ConversationProfileApplicationService applicationService;

    @MockBean
    private SkillCatalogApplicationService skillCatalogApplicationService;

    @MockBean
    private LocalUserContextResolver userContextResolver;

    @BeforeEach
    void setUpUserContext() {
        // 这个函数的作用是让每个 Controller 测试都在同一个已登录用户上下文中执行。
        given(userContextResolver.resolve(nullable(String.class), anyString())).willReturn("user-001");
    }

    @Test
    void shouldListProfiles() throws Exception {
        // 这个测试函数的作用是验证会话设定列表接口会直接返回后端保存的 profile 列表。
        given(applicationService.listProfiles("user-001"))
                .willReturn(List.of(profileResponse()));

        mockMvc.perform(get("/internal/conversation-profiles"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$[0].name").value("重要联系人自动回"))
                .andExpect(jsonPath("$[0].preferredRoute").value("social_reply"))
                .andExpect(jsonPath("$[0].replyMode").value("AUTO_REPLY"))
                .andExpect(jsonPath("$[0].accountId").value("3969785168"))
                .andExpect(jsonPath("$[0].skillReferences[0]").value("github://demo/skill"));

        verify(applicationService).listProfiles("user-001");
    }

    @Test
    void shouldReturnProfileConfiguration() throws Exception {
        // 这个测试函数的作用是验证前端配置辅助接口会返回枚举、工具列表和可选 skill 列表。
        given(skillCatalogApplicationService.buildConversationProfileConfiguration())
                .willReturn(new ConversationProfileConfigurationResponse(
                        List.of("qq", "wechat"),
                        List.of("life", "work"),
                        List.of("private", "group"),
                        List.of("ALWAYS", "AT_SELF_ONLY"),
                        List.of("AUTO_REPLY", "DRAFT_ONLY", "SILENT"),
                        List.of("NONE", "PROMPT", "SKILL"),
                        List.of("social_reply", "task_plan"),
                        List.of("send_qq_message", "create_task"),
                        List.of(skillDescriptorResponse())
                ));

        mockMvc.perform(get("/internal/conversation-profiles/configuration"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.supportedPlatforms[0]").value("qq"))
                .andExpect(jsonPath("$.personaModes[2]").value("SKILL"))
                .andExpect(jsonPath("$.availableTools[1]").value("create_task"))
                .andExpect(jsonPath("$.availableSkills[0].reference").value("skills/personas/reliable-assistant"));

        verify(skillCatalogApplicationService).buildConversationProfileConfiguration();
    }

    @Test
    void shouldCreateProfile() throws Exception {
        // 这个测试函数的作用是验证创建会话设定接口会把前端提交的字段交给应用服务并返回新 profile。
        given(applicationService.createProfile(eq("user-001"), any(ConversationProfileUpsertRequest.class)))
                .willReturn(profileResponse());

        mockMvc.perform(post("/internal/conversation-profiles")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {
                                  "name": "重要联系人自动回",
                                  "platform": "qq",
                                  "accountId": "3969785168",
                                  "scene": "life",
                                  "chatType": "private",
                                  "chatIds": ["2597164807"],
                                  "targetUserIds": ["10001"],
                                  "supportedRoutes": ["social_reply"],
                                  "triggerMode": "KEYWORD_ONLY",
                                  "triggerKeywords": ["紧急", "马上"],
                                  "personaMode": "SKILL",
                                  "skillReference": "github://demo/skill",
                                  "skillReferences": ["github://demo/skill", "github://demo/skill-2"],
                                  "modelProfileId": "model-profile-001",
                                  "preferredRoute": "social_reply",
                                  "replyMode": "AUTO_REPLY",
                                  "allowedTools": ["send_qq_message"],
                                  "priority": 10
                                }
                                """))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.name").value("重要联系人自动回"))
                .andExpect(jsonPath("$.priority").value(10))
                .andExpect(jsonPath("$.modelProfileId").value("model-profile-001"));

        verify(applicationService).createProfile(eq("user-001"), any(ConversationProfileUpsertRequest.class));
    }

    @Test
    void shouldMatchProfile() throws Exception {
        // 这个测试函数的作用是验证会话设定匹配接口会返回命中状态、激活状态和命中的 profile 详情。
        given(applicationService.matchProfile(eq("user-001"), any(ConversationProfileMatchRequest.class)))
                .willReturn(new ConversationProfileMatchResponse(
                        true,
                        true,
                        "命中会话范围且满足触发条件",
                        profileResponse()
                ));

        mockMvc.perform(post("/internal/conversation-profiles/match")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {
                                  "platform": "qq",
                                  "accountId": "3969785168",
                                  "scene": "life",
                                  "chatType": "private",
                                  "chatId": "2597164807",
                                  "senderId": "10001",
                                  "route": "social_reply",
                                  "text": "紧急，麻烦马上回复",
                                  "atSelf": false
                                }
                                """))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.matched").value(true))
                .andExpect(jsonPath("$.active").value(true))
                .andExpect(jsonPath("$.profile.name").value("重要联系人自动回"))
                .andExpect(jsonPath("$.profile.supportedRoutes[0]").value("social_reply"));

        verify(applicationService).matchProfile(eq("user-001"), any(ConversationProfileMatchRequest.class));
    }

    @Test
    void shouldDeleteProfile() throws Exception {
        // 这个测试函数的作用是验证删除接口会把 profileId 正确传给应用服务。
        mockMvc.perform(delete("/internal/conversation-profiles/profile-001"))
                .andExpect(status().isNoContent());

        verify(applicationService).deleteProfile(eq("user-001"), eq("profile-001"));
    }

    @Test
    void shouldUpdateProfile() throws Exception {
        // 这个测试函数的作用是验证更新接口会返回修改后的会话设定。
        given(applicationService.updateProfile(eq("user-001"), eq("profile-001"), any(ConversationProfileUpsertRequest.class)))
                .willReturn(profileResponse());

        mockMvc.perform(put("/internal/conversation-profiles/profile-001")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {
                                  "name": "重要联系人自动回",
                                  "platform": "qq",
                                  "accountId": "3969785168",
                                  "scene": "life",
                                  "chatType": "private"
                                }
                                """))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.id").value("profile-001"));

        verify(applicationService).updateProfile(eq("user-001"), eq("profile-001"), any(ConversationProfileUpsertRequest.class));
    }

    private ConversationProfileResponse profileResponse() {
        // 这个函数的作用是统一构造一个带 skill、工具白名单和模型配置绑定的 profile 响应，供多个测试复用。
        return new ConversationProfileResponse(
                "profile-001",
                "重要联系人自动回",
                "用于演示私聊人格设定",
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
                10,
                Instant.parse("2026-07-09T00:00:00Z"),
                Instant.parse("2026-07-09T00:10:00Z")
        );
    }

    private SkillDescriptorResponse skillDescriptorResponse() {
        // 这个函数的作用是构造一个前端配置页用到的示例 skill 描述响应。
        return new SkillDescriptorResponse(
                "persona.reliable_assistant",
                "可靠助理人格",
                "1.0.0",
                "persona",
                "适合私聊回复",
                "builtin",
                "skills/personas/reliable-assistant",
                List.of("social_reply"),
                new SkillPromptFragmentsResponse("回复时保持冷静、可靠。"),
                new SkillToolPolicyResponse(List.of("send_qq_message")),
                new SkillModelHintsResponse(0.4, 512),
                true,
                "agent-runtime-python/skills/personas/reliable-assistant"
        );
    }
}
