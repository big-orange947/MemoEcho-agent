package com.memoecho.eventcenter.controller;

import com.memoecho.eventcenter.dto.SkillDescriptorResponse;
import com.memoecho.eventcenter.dto.SkillInstallResponse;
import com.memoecho.eventcenter.dto.SkillModelHintsResponse;
import com.memoecho.eventcenter.dto.SkillPromptFragmentsResponse;
import com.memoecho.eventcenter.dto.SkillResolvePreviewResponse;
import com.memoecho.eventcenter.dto.SkillToolPolicyResponse;
import com.memoecho.eventcenter.service.SkillCatalogApplicationService;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;

import java.util.List;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.BDDMockito.given;
import static org.mockito.Mockito.verify;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@WebMvcTest(InternalSkillController.class)
class InternalSkillControllerTest {

    @Autowired
    private MockMvc mockMvc;

    @MockBean
    private SkillCatalogApplicationService skillCatalogApplicationService;

    @Test
    void shouldListSkills() throws Exception {
        // 这个测试函数的作用是验证 skill 列表接口会把当前可用 skill 返回给前端。
        given(skillCatalogApplicationService.listSkills())
                .willReturn(List.of(skillDescriptorResponse()));

        mockMvc.perform(get("/internal/skills"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$[0].id").value("persona.reliable_assistant"))
                .andExpect(jsonPath("$[0].sourceType").value("builtin"))
                .andExpect(jsonPath("$[0].reference").value("skills/personas/reliable-assistant"));

        verify(skillCatalogApplicationService).listSkills();
    }

    @Test
    void shouldInstallGithubSkill() throws Exception {
        // 这个测试函数的作用是验证 GitHub skill 安装接口会返回安装结果和落地后的 descriptor 信息。
        given(skillCatalogApplicationService.installGithubSkill(any()))
                .willReturn(new SkillInstallResponse(
                        "installed",
                        "github://demo-owner/demo-repo/personas/reliable-assistant",
                        "github://demo-owner/demo-repo/personas/reliable-assistant",
                        "github",
                        "D:/project/memo_echo-agent/agent-runtime-python/skills-installed/github/demo-owner/demo-repo/main/personas/reliable-assistant",
                        skillDescriptorResponse()
                ));

        mockMvc.perform(post("/internal/skills/install/github")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {
                                  "reference": "github://demo-owner/demo-repo/personas/reliable-assistant"
                                }
                                """))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("installed"))
                .andExpect(jsonPath("$.sourceType").value("github"));

        verify(skillCatalogApplicationService).installGithubSkill(any());
    }

    @Test
    void shouldPreviewResolveSkills() throws Exception {
        // 这个测试函数的作用是验证 skill 预览解析接口会区分已解析成功和仍未安装的 skill 引用。
        given(skillCatalogApplicationService.previewResolve(any()))
                .willReturn(new SkillResolvePreviewResponse(
                        List.of(skillDescriptorResponse()),
                        List.of("github://demo-owner/demo-repo/work/project-manager")
                ));

        mockMvc.perform(post("/internal/skills/resolve-preview")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {
                                  "route": "social_reply",
                                  "skillReferences": [
                                    "skills/personas/reliable-assistant",
                                    "github://demo-owner/demo-repo/work/project-manager"
                                  ]
                                }
                                """))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.resolvedSkills[0].id").value("persona.reliable_assistant"))
                .andExpect(jsonPath("$.unresolvedSkillReferences[0]").value("github://demo-owner/demo-repo/work/project-manager"));

        verify(skillCatalogApplicationService).previewResolve(any());
    }

    private SkillDescriptorResponse skillDescriptorResponse() {
        // 这个函数的作用是统一构造 controller 测试需要的 skill 响应样例。
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
